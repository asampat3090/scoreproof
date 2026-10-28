"""Tests for the MusicXML exporter-quirk repairs.

Each quirk test checks two things: the rule rewrites the XML, and music21
reads the repaired file correctly (the reason the rule exists).
"""

import io
import xml.etree.ElementTree as ET
import zipfile

import pytest
from score_xml import attributes, note, score

from songchain.errors import ScoreError
from songchain.score import musicxml


def parse(xml):
    import music21

    return music21.converter.parseData(xml, format="musicxml")


def measure_offsets(stream):
    return [m.offset for m in stream.parts[0].getElementsByClass("Measure")]


def rules(result):
    return {f.rule: f.count for f in result.fixes}


def mxl(files):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, data in files.items():
            archive.writestr(name, data)
    return buffer.getvalue()


CONTAINER = (
    '<container><rootfiles><rootfile full-path="{path}"/></rootfiles></container>'
)


class TestReadDocument:
    def test_plain_xml_is_returned_unchanged(self):
        assert musicxml.read_document(b"<x/>") == b"<x/>"

    def test_mxl_uses_container_rootfile(self):
        data = mxl(
            {
                "META-INF/container.xml": CONTAINER.format(path="score.xml"),
                "other.xml": "<other/>",
                "score.xml": "<score/>",
            }
        )
        assert musicxml.read_document(data) == b"<score/>"

    def test_mxl_without_container_uses_first_xml(self):
        assert musicxml.read_document(mxl({"a.musicxml": "<a/>"})) == b"<a/>"

    def test_mxl_with_broken_container_falls_back(self):
        data = mxl(
            {
                "META-INF/container.xml": CONTAINER.format(path="missing.xml"),
                "real.xml": "<real/>",
            }
        )
        assert musicxml.read_document(data) == b"<real/>"

    def test_mxl_without_document_raises(self):
        with pytest.raises(ScoreError, match="No MusicXML"):
            musicxml.read_document(mxl({"readme.txt": "hi"}))

    def test_oversized_document_raises(self, monkeypatch):
        monkeypatch.setattr(musicxml, "MAX_DOCUMENT_BYTES", 3)
        with pytest.raises(ScoreError, match="too large"):
            musicxml.read_document(mxl({"a.xml": "<abcdef/>"}))


class TestNormalize:
    def test_invalid_xml_raises(self):
        with pytest.raises(ScoreError, match="Not valid XML"):
            musicxml.normalize(b"<unclosed>")

    def test_clean_file_needs_no_fixes(self):
        result = musicxml.normalize(score([attributes() + note()]).encode())
        assert result.fixes == []
        assert result.software == "Test Exporter 1.0"
        assert result.xml.startswith(b"<?xml")

    def test_utf16_is_reencoded(self):
        result = musicxml.normalize(score([attributes() + note()]).encode("utf-16"))
        assert rules(result) == {"utf16": 1}
        assert b"Test Exporter" in result.xml

    def test_timewise_scores_are_left_alone(self):
        xml = score([""], name="", root="score-timewise")
        assert musicxml.normalize(xml.encode()).fixes == []


class TestFillNoteFreeBars:
    TEMPO_RAMP = "".join(
        f'<sound tempo="{130 - i}"><offset>{i}</offset></sound>' for i in range(1, 12)
    )

    def build(self, middle, first_extra=""):
        whole = note(duration=12, kind="half", inner="<dot/>")
        return score(
            [attributes(divisions=4, beats=3) + first_extra + whole, middle, whole]
        )

    def test_dorico_tempo_ramp_bar_no_longer_drifts(self):
        xml = self.build(self.TEMPO_RAMP + "<forward><duration>12</duration></forward>")
        assert measure_offsets(parse(xml)) == [0.0, 3.0, 5.75]  # the bug

        result = musicxml.normalize(xml.encode())
        assert rules(result) == {"fill_note_free_bars": 1}
        repaired = parse(result.xml.decode())
        assert measure_offsets(repaired) == [0.0, 3.0, 6.0]
        assert len(repaired.recurse().getElementsByClass("MetronomeMark")) == 11

    def test_empty_bar_gets_rest_from_time_signature(self):
        result = musicxml.normalize(self.build("").encode())
        rest = ET.fromstring(result.xml).find(".//measure[@number='2']/note")
        assert rest.find("rest").get("measure") == "yes"
        assert rest.findtext("duration") == "12"

    def test_rest_goes_before_right_barline(self):
        xml = self.build(
            '<barline location="right"><bar-style>light-heavy</bar-style>' "</barline>"
        )
        measure = ET.fromstring(musicxml.normalize(xml.encode()).xml).find(
            ".//measure[@number='2']"
        )
        assert [e.tag for e in measure] == ["note", "barline"]

    def test_left_barline_stays_first(self):
        xml = self.build(
            '<barline location="left"><repeat direction="forward"/>' "</barline>"
        )
        measure = ET.fromstring(musicxml.normalize(xml.encode()).xml).find(
            ".//measure[@number='2']"
        )
        assert [e.tag for e in measure] == ["barline", "note"]

    def test_implicit_empty_bar_is_skipped(self):
        xml = score([attributes() + note()]).replace(
            "</part>", '<measure number="2" implicit="yes"/></part>'
        )
        assert musicxml.normalize(xml.encode()).fixes == []

    def test_bars_without_divisions_are_skipped(self):
        assert musicxml.fill_note_free_bars(ET.fromstring(score([""]))) == 0

    def test_senza_misura_empty_bar_is_skipped(self):
        free = "<attributes><divisions>1</divisions><time><senza-misura/></time>"
        xml = score([free + "</attributes>"])
        assert musicxml.fill_note_free_bars(ET.fromstring(xml)) == 0


class TestMoveDirectionsOutOfChords:
    WORDS = "<direction><direction-type><words>let ring</words></direction-type>"

    def chord_bar(self):
        return (
            attributes()
            + note("C")
            + self.WORDS
            + "</direction>"
            + note("E", before="<chord/>")
            + note("G", before="<chord/>")
        )

    def test_guitar_pro_chord_is_rejoined(self):
        xml = score([self.chord_bar(), note("D")])
        assert measure_offsets(parse(xml)) == [0.0, 8.0]  # the bug

        result = musicxml.normalize(xml.encode())
        assert rules(result) == {"move_directions_out_of_chords": 1}
        repaired = parse(result.xml.decode())
        assert measure_offsets(repaired) == [0.0, 4.0]
        chords = [n for n in repaired.recurse().notes if n.isChord]
        assert [len(c.pitches) for c in chords] == [3]

    def test_direction_before_a_plain_note_is_untouched(self):
        xml = score([attributes() + self.WORDS + "</direction>" + note()])
        assert musicxml.normalize(xml.encode()).fixes == []

    def test_backup_breaks_the_chord(self):
        bar = (
            attributes()
            + note("C")
            + "<backup><duration>4</duration></backup>"
            + self.WORDS
            + "</direction>"
            + note("E", before="<chord/>")
        )
        root = ET.fromstring(score([bar]))
        assert musicxml.move_directions_out_of_chords(root) == 0


class TestHoistNoteDynamics:
    FORTE = "<notations><dynamics><f/></dynamics></notations>"

    def test_notion_dynamics_are_kept(self):
        xml = score([attributes() + note(inner=self.FORTE), note("D")])
        assert len(parse(xml).recurse().getElementsByClass("Dynamic")) == 0  # bug

        result = musicxml.normalize(xml.encode())
        assert rules(result) == {"hoist_note_dynamics": 1}
        dynamics = parse(result.xml.decode()).recurse().getElementsByClass("Dynamic")
        assert [d.value for d in dynamics] == ["f"]

    def test_chord_member_dynamic_goes_before_chord_root(self):
        bar = attributes() + note("C") + note("E", before="<chord/>", inner=self.FORTE)
        root = ET.fromstring(score([bar]))
        assert musicxml.hoist_note_dynamics(root) == 1
        assert [e.tag for e in root.find(".//measure")] == [
            "attributes",
            "direction",
            "note",
            "note",
        ]

    def test_staff_and_other_notations_are_preserved(self):
        inner = (
            "<staff>2</staff><notations><dynamics placement='above'><p/></dynamics>"
            '<tied type="start"/></notations>'
        )
        root = ET.fromstring(score([attributes() + note(inner=inner)]))
        musicxml.hoist_note_dynamics(root)
        direction = root.find(".//direction")
        assert direction.get("placement") == "above"
        assert direction.findtext("staff") == "2"
        assert root.find(".//note/notations/tied") is not None


class TestCompleteTransposes:
    def test_flat_octave_transpose_no_longer_crashes(self):
        extra = "<transpose><octave-change>-1</octave-change></transpose>"
        xml = score([attributes(extra=extra) + note()])
        with pytest.raises(TypeError):
            parse(xml)  # the bug

        result = musicxml.normalize(xml.encode())
        assert rules(result) == {"complete_transposes": 2}
        part = parse(result.xml.decode()).parts[0]
        assert part.getInstrument().transposition.semitones == -12

    def test_diatonic_is_derived_from_chromatic(self):
        extra = (
            "<transpose><chromatic>-2</chromatic><octave-change>-1</octave-change>"
            "</transpose>"
        )
        root = ET.fromstring(score([attributes(extra=extra)]))
        assert musicxml.complete_transposes(root) == 1
        assert root.findtext(".//transpose/diatonic") == "-1"
        assert musicxml._diatonic_steps(14) == 8

    def test_chromatic_only_transpose_is_untouched(self):
        extra = "<transpose><chromatic>-2</chromatic></transpose>"
        root = ET.fromstring(score([attributes(extra=extra)]))
        assert musicxml.complete_transposes(root) == 0
        assert root.find(".//transpose/diatonic") is None

    def test_chromatic_goes_after_diatonic(self):
        extra = "<transpose><diatonic>0</diatonic><octave-change>1</octave-change>"
        root = ET.fromstring(score([attributes(extra=extra + "</transpose>")]))
        assert musicxml.complete_transposes(root) == 1
        assert [e.tag for e in root.find(".//transpose")] == [
            "diatonic",
            "chromatic",
            "octave-change",
        ]


class TestRepairDanglingTies:
    START = '<tie type="start"/><notations><tied type="start"/></notations>'
    STOP = '<tie type="stop"/><notations><tied type="stop"/></notations>'

    def ties_in(self, xml):
        root = ET.fromstring(xml)
        return root, musicxml.repair_dangling_ties(root)

    def test_tie_into_a_different_note_is_removed(self):
        xml = score([attributes() + note(inner=self.START), note("C")])
        root, count = self.ties_in(xml)
        assert count == 1
        assert root.find(".//tie") is None
        assert root.find(".//notations") is None

    def test_tie_at_end_of_part_is_removed(self):
        _, count = self.ties_in(score([attributes() + note(inner=self.START)]))
        assert count == 1

    def test_matched_tie_is_kept(self):
        xml = score([attributes() + note(inner=self.START), note(inner=self.STOP)])
        root, count = self.ties_in(xml)
        assert count == 0
        assert len(root.findall(".//tie")) == 2

    def test_rests_and_stray_stops_are_ignored(self):
        rest = "<note><rest/><duration>4</duration></note>"
        xml = score([attributes() + rest + note(inner=self.STOP)])
        assert self.ties_in(xml)[1] == 0


class TestNameParts:
    def names(self, xml):
        root = ET.fromstring(xml)
        count = musicxml.name_parts(root)
        return count, [p.findtext("part-name") for p in root.iter("score-part")]

    def test_finale_default_name_uses_instrument(self):
        instrument = (
            '<score-instrument id="I1"><instrument-name>Violin</instrument-name>'
            "</score-instrument>"
        )
        xml = score([""], name="MusicXML Part", score_part_extra=instrument)
        assert self.names(xml) == (1, ["Violin"])

    def test_empty_name_without_instrument_is_numbered(self):
        assert self.names(score([""], name="")) == (1, ["Part 1"])

    def test_missing_part_name_element_is_added(self):
        xml = score([""]).replace("<part-name>Piano</part-name>", "")
        assert self.names(xml) == (1, ["Part 1"])

    def test_real_names_are_kept(self):
        assert self.names(score([""])) == (0, ["Piano"])


class TestIrregularBars:
    def test_underfull_middle_bar_is_reported(self):
        half = note(duration=2, kind="half")
        xml = score([attributes() + note(), half, note(), note()])
        assert musicxml.normalize(xml.encode()).irregular_bars == [
            {"part": "P1", "measure": "2", "length": 2.0, "expected": 4.0}
        ]

    def test_pickup_final_implicit_and_undivided_bars_are_skipped(self):
        half = note(duration=2, kind="half")
        xml = score([attributes() + half, note(), half, half]).replace(
            '<measure number="3">', '<measure number="3" implicit="yes">'
        )
        assert musicxml.normalize(xml.encode()).irregular_bars == []
        assert musicxml.find_irregular_bars(ET.fromstring(score([""] * 3))) == []

    def test_grace_notes_chords_and_backups_are_measured(self):
        bar = (
            "<note><grace/><pitch><step>D</step><octave>4</octave></pitch>"
            "<voice>1</voice></note>"
            + note()
            + note("E", before="<chord/>")
            + "<backup><duration>8</duration></backup>"
            + "<forward><duration>4</duration></forward>"
        )
        xml = score([attributes() + note(), bar, note()])
        assert musicxml.normalize(xml.encode()).irregular_bars == []


class TestTimeLength:
    def time(self, inner):
        return ET.fromstring(f"<attributes><time>{inner}</time></attributes>")

    def test_additive_meter(self):
        bar = musicxml._time_length(
            self.time("<beats>3+2</beats><beat-type>8</beat-type>"), None
        )
        assert bar == 2.5

    def test_unreadable_meter_is_unknown(self):
        bad = self.time("<beats>x</beats><beat-type>4</beat-type>")
        assert musicxml._time_length(bad, 4) is None

    def test_no_time_keeps_current(self):
        assert musicxml._time_length(ET.fromstring("<attributes/>"), 3) == 3


def test_elements_without_duration_count_as_zero():
    bar = "<forward/>" + note()
    root = ET.fromstring(score([attributes() + bar]))
    assert musicxml._measure_extent(root.find(".//measure"), 1) == 4


class TestUnsupportedAccidentals:
    def test_natural_flat_key_signature_crash_is_fixed(self):
        key = (
            "<key><key-step>B</key-step><key-alter>-1</key-alter>"
            "<key-accidental>natural-flat</key-accidental></key>"
        )
        xml = score([attributes(extra=key) + note()])
        with pytest.raises(Exception, match="natural-flat"):
            parse(xml)  # the bug

        result = musicxml.normalize(xml.encode())
        assert rules(result) == {"drop_unsupported_accidentals": 1}
        assert ET.fromstring(result.xml).findtext(".//key-accidental") == "flat"
        parse(result.xml.decode())

    def test_microtonal_and_empty_accidentals_are_removed(self):
        sagittal = '<accidental smufl="accSagittal5CommaDown">other</accidental>'
        key = (
            "<key><key-step>B</key-step><key-alter>-1</key-alter>"
            "<key-accidental></key-accidental></key>"
        )
        root = ET.fromstring(score([attributes(extra=key) + note(inner=sagittal)]))
        assert musicxml.drop_unsupported_accidentals(root) == 2
        assert root.find(".//accidental") is None
        assert root.find(".//key-accidental") is None

    def test_supported_accidentals_are_kept(self):
        root = ET.fromstring(
            score([note(inner="<accidental>sharp-sharp</accidental>")])
        )
        assert musicxml.drop_unsupported_accidentals(root) == 0


class TestNoteheads:
    def test_noteflight_named_notehead_is_readable(self):
        named = '<notehead smufl="noteheadSlashHorizontalEnds">named</notehead>'
        xml = score([attributes() + note(inner=named)])
        with pytest.raises(Exception, match="named"):
            parse(xml)  # the bug

        result = musicxml.normalize(xml.encode())
        assert rules(result) == {"normalize_noteheads": 1}
        parse(result.xml.decode())

    def test_known_noteheads_are_kept(self):
        root = ET.fromstring(score([note(inner="<notehead>X</notehead>")]))
        assert musicxml.normalize_noteheads(root) == 0


class TestTimeSignatures:
    def meter(self, beats, beat_type):
        time = f"<time><beats>{beats}</beats><beat-type>{beat_type}</beat-type></time>"
        bar = f"<attributes><divisions>4</divisions>{time}</attributes>"
        return score([bar + note(duration=1, kind="16th")])

    @pytest.mark.parametrize(
        "beats, beat_type, expected",
        [
            ("3.5", "8", ("7", "16")),
            ("4", "256", ("1", "64")),
            ("4", "1024", ("1", "256")),
        ],
    )
    def test_exotic_meters_become_exact_equivalents(self, beats, beat_type, expected):
        root = ET.fromstring(self.meter(beats, beat_type))
        assert musicxml.simplify_time_signatures(root) == 1
        time = root.find(".//time")
        assert (time.findtext("beats"), time.findtext("beat-type")) == expected

    def test_fractional_meter_crash_is_fixed(self):
        xml = self.meter("3.5", "8")
        with pytest.raises(Exception, match="3.5/8"):
            parse(xml)  # the bug
        parse(musicxml.normalize(xml.encode()).xml.decode())

    @pytest.mark.parametrize(
        "beats, beat_type",
        [("4", "4"), ("3+2", "8"), ("x", "4"), ("1", "3"), ("1", "1024")],
    )
    def test_ordinary_or_unfixable_meters_are_left(self, beats, beat_type):
        root = ET.fromstring(self.meter(beats, beat_type))
        assert musicxml.simplify_time_signatures(root) == 0


class TestFunctionHarmonies:
    def harmony(self, inner):
        return f'<harmony placement="below">{inner}</harmony>'

    def test_roman_numeral_becomes_text(self):
        roman = self.harmony('<function>III</function><kind text="7">none</kind>')
        xml = score([attributes() + roman + note()])
        result = musicxml.normalize(xml.encode())
        assert rules(result) == {"convert_function_harmonies": 1}
        direction = ET.fromstring(result.xml).find(".//direction")
        assert direction.findtext(".//words") == "III7"
        assert direction.get("placement") == "below"
        assert ET.fromstring(result.xml).find(".//harmony") is None

    def test_nashville_number_crash_is_fixed(self):
        nashville = self.harmony(
            "<root><root-step>6</root-step></root><kind>minor</kind>"
        )
        xml = score([attributes() + nashville + note()])
        with pytest.raises(Exception, match="'6'"):
            parse(xml)  # the bug
        result = musicxml.normalize(xml.encode())
        assert ET.fromstring(result.xml).findtext(".//words") == "6"
        parse(result.xml.decode())

    def test_numeral_and_empty_harmonies(self):
        numeral = self.harmony("<numeral><numeral-root>4</numeral-root></numeral>")
        empty = "<harmony/>"
        root = ET.fromstring(score([attributes() + numeral + empty + note()]))
        assert musicxml.convert_function_harmonies(root) == 2
        assert [w.text for w in root.iter("words")] == ["4", "?"]

    def test_chord_symbols_with_roots_are_kept(self):
        chord = self.harmony("<root><root-step>C</root-step></root><kind>major</kind>")
        root = ET.fromstring(score([attributes() + chord + note()]))
        assert musicxml.convert_function_harmonies(root) == 0
