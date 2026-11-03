"""Tests for score formats, loading, fidelity measurement and conversion."""

import pytest
from score_xml import attributes, note, score

from scoreproof import FORMATS, convert, detect_format, fidelity, get_format, load
from scoreproof.convert import batch_convert
from scoreproof.errors import ScoreError
from scoreproof import io as score_io


@pytest.fixture
def chorale():
    """A real four-part Bach chorale bundled with music21 (BSD licensed)."""
    import music21

    return music21.corpus.getWork("bach/bwv66.6")


@pytest.fixture
def quirky_file(tmp_path):
    """A MusicXML file with an octave-only transpose (crashes plain music21)."""
    extra = "<transpose><octave-change>-1</octave-change></transpose>"
    path = tmp_path / "guitar.musicxml"
    path.write_text(score([attributes(extra=extra) + note(), note("D")]))
    return path


class TestFormats:
    def test_lookup_by_name_and_extension(self):
        assert get_format("MIDI").name == "midi"
        assert get_format(".krn").name == "humdrum"
        assert get_format("mid").name == "midi"
        assert FORMATS["mxl"].extension == ".mxl"

    def test_unknown_format_lists_supported(self):
        with pytest.raises(ScoreError, match="Supported: musicxml"):
            get_format("sib")

    def test_detect_by_extension(self, tmp_path):
        assert detect_format(tmp_path / "a.musicxml").name == "musicxml"
        assert detect_format(tmp_path / "missing.xml").name == "musicxml"

    def test_detect_sniffs_mei_in_xml(self, tmp_path):
        mei = tmp_path / "a.xml"
        mei.write_text(
            '<?xml version="1.0"?><mei xmlns="http://www.music-encoding.org/ns/mei"/>'
        )
        assert detect_format(mei).name == "mei"
        plain = tmp_path / "b.xml"
        plain.write_text(score([attributes() + note()]))
        assert detect_format(plain).name == "musicxml"

    def test_detect_needs_an_extension(self, tmp_path):
        with pytest.raises(ScoreError, match="no extension"):
            detect_format(tmp_path / "README")


class TestLoad:
    def test_missing_file(self, tmp_path):
        with pytest.raises(ScoreError, match="File not found"):
            load(tmp_path / "nope.musicxml")

    def test_musicxml_is_repaired_on_load(self, quirky_file):
        loaded = load(quirky_file)
        assert loaded.format.name == "musicxml"
        assert loaded.software == "Test Exporter 1.0"
        assert [f.rule for f in loaded.fixes] == ["complete_transposes"]
        assert loaded.source == quirky_file

    def test_compressed_corpus_file(self, chorale):
        loaded = load(chorale)
        assert loaded.format.name == "mxl"
        assert len(loaded.stream.parts) == 4

    def test_explicit_format_overrides_extension(self, tmp_path, chorale):
        path = tmp_path / "chorale.txt"
        load(chorale).save(tmp_path / "chorale.abc")
        path.write_bytes((tmp_path / "chorale.abc").read_bytes())
        assert load(path, format="abc").format.name == "abc"

    def test_unreadable_file_raises_score_error(self, tmp_path):
        bad = tmp_path / "broken.mid"
        bad.write_bytes(b"not a midi file")
        with pytest.raises(ScoreError, match="Could not read midi"):
            load(bad)

    def test_multi_tune_abc_returns_first_tune(self, tmp_path):
        abc = tmp_path / "tunes.abc"
        abc.write_text("X:1\nT:One\nK:C\nCDEF|\n\nX:2\nT:Two\nK:G\nGABc|\n")
        loaded = load(abc)
        assert [p.nameWithOctave for p in loaded.stream.recurse().pitches][:1] == ["C4"]


class TestSave:
    def test_save_picks_format_from_extension(self, tmp_path, chorale):
        written = load(chorale).save(tmp_path / "out.mid")
        assert written.read_bytes()[:4] == b"MThd"

    def test_write_failure_raises_score_error(self, tmp_path, chorale, monkeypatch):
        loaded = load(chorale)

        def broken_write(*args, **kwargs):
            raise ValueError("disk full")

        monkeypatch.setattr(loaded.stream, "write", broken_write)
        with pytest.raises(ScoreError, match="Could not write abc: ValueError"):
            loaded.save(tmp_path / "out.abc")


class TestFidelity:
    def test_identical_streams_are_exact(self, chorale):
        stream = load(chorale).stream
        report = fidelity.compare(stream, stream)
        assert report["confidence"] == "exact"
        assert report["note_recall"] == report["note_precision"] == 1.0
        assert report["source"]["parts"] == 4

    def test_lost_notes_lower_confidence(self, chorale):
        import music21

        stream = load(chorale).stream
        thinned = stream.__deepcopy__()
        for n in list(thinned.parts[0].recurse().notes)[:20]:
            n.activeSite.remove(n)
        report = fidelity.compare(stream, thinned)
        assert report["confidence"] == "low"
        assert report["note_recall"] < 0.95
        assert report["note_precision"] == 1.0
        _ = music21

    @pytest.mark.parametrize(
        "recall, expected", [(1.0, "high"), (0.995, "high"), (0.96, "medium")]
    )
    def test_confidence_levels(self, recall, expected):
        src = fidelity.Summary(1, 1, 10, 4.0, 0, 0, 0, 0)
        out = fidelity.Summary(1, 1, 10, 5.0, 0, 0, 0, 0)
        assert fidelity._confidence(recall, 1.0, src, out) == expected

    def test_empty_streams_count_as_kept(self):
        import music21

        empty = music21.stream.Score([music21.stream.Part([music21.stream.Measure()])])
        assert fidelity.compare(empty, empty)["note_recall"] == 1.0

    def test_grace_notes_and_chord_symbols_are_not_counted_as_notes(self):
        import music21

        measure = music21.stream.Measure()
        measure.append(music21.harmony.ChordSymbol("C"))
        measure.append(music21.note.Note("C4").getGrace())
        measure.append(music21.note.Note("E4", quarterLength=4))
        stream = music21.stream.Score([music21.stream.Part([measure])])
        summary = fidelity.summarize(stream)
        assert (summary.notes, summary.chord_symbols) == (1, 1)

    def test_percussion_notes_are_counted(self):
        import music21

        snare = music21.note.Unpitched(displayName="C5", quarterLength=2)
        kit = music21.percussion.PercussionChord(
            [music21.note.Unpitched(displayName="F4"), music21.note.Note("C5")],
            quarterLength=2,
        )
        stream = music21.stream.Score(
            [music21.stream.Part([music21.stream.Measure([snare, kit])])]
        )
        events = fidelity.note_events(stream)
        assert sum(events.values()) == 3
        assert (0.0, "unpitched C5", 2.0) in events

    def test_unexpandable_repeats_compare_as_written(self, chorale, monkeypatch):
        stream = load(chorale).stream

        def broken(*args, **kwargs):
            raise ValueError("bad repeats")

        monkeypatch.setattr(stream, "expandRepeats", broken)
        assert fidelity.expand_repeats(stream) is stream


class TestConvert:
    @pytest.mark.parametrize(
        "target", ["musicxml", "mxl", "midi", "abc", "mei", "humdrum"]
    )
    def test_chorale_survives_every_format(self, tmp_path, chorale, target):
        result = convert(chorale, tmp_path / "chorale", to=target)
        assert result.output.suffix == FORMATS[target].extension
        assert result.output.exists()
        assert result.confidence == "exact"
        assert result.source["notes"] == result.result["notes"] == 163

    def test_target_inferred_from_destination(self, tmp_path, quirky_file):
        result = convert(quirky_file, tmp_path / "out.mid")
        assert (result.source_format, result.target_format) == ("musicxml", "midi")
        assert "lyrics" in result.expected_losses
        assert result.fixes[0]["rule"] == "complete_transposes"

    def test_result_serializes(self, tmp_path, quirky_file):
        data = convert(quirky_file, tmp_path / "out.abc").to_dict()
        assert data["output"].endswith("out.abc")
        assert data["target_format"] == "abc"

    def test_midi_compares_against_performed_repeats(self, tmp_path, monkeypatch):
        calls = []
        original = fidelity.compare

        def spy(source, output, performed=False):
            calls.append(performed)
            return original(source, output, performed)

        monkeypatch.setattr(fidelity, "compare", spy)
        path = tmp_path / "a.musicxml"
        path.write_text(score([attributes() + note(), note("D")]))
        convert(path, tmp_path / "a.mid")
        convert(path, tmp_path / "a.abc")
        assert calls == [True, False]


def test_registration_happens_once(monkeypatch, quirky_file):
    monkeypatch.setattr(score_io, "_registered", False)
    load(quirky_file)
    assert score_io._registered is True


class TestConvertDestinations:
    def test_needs_a_target(self, quirky_file):
        with pytest.raises(ScoreError, match="Say which format"):
            convert(quirky_file)

    def test_default_output_never_overwrites(self, quirky_file):
        first = convert(quirky_file, to="abc").output
        second = convert(quirky_file, to="abc").output
        assert (first.name, second.name) == ("guitar.abc", "guitar-2.abc")

    def test_folder_destination(self, quirky_file, tmp_path):
        out = tmp_path / "out"
        out.mkdir()
        assert convert(quirky_file, out, to="mei").output == out / "guitar.mei"


class TestBatchConvert:
    def test_folder_with_a_broken_file(self, tmp_path, quirky_file):
        (tmp_path / "broken.mid").write_bytes(b"not midi")
        (tmp_path / "notes.txt").write_text("ignored")
        progress = []
        report = batch_convert(
            [tmp_path],
            "abc",
            tmp_path / "out",
            lambda done, total: progress.append(done),
        )
        assert (report["converted"], report["failed"]) == (1, 1)
        assert "Could not read midi" in report["files"][0]["error"]
        assert report["files"][1]["confidence"] in ("exact", "high")
        assert progress == [1, 2]

    def test_single_file_string(self, quirky_file):
        assert batch_convert(str(quirky_file), "midi")["converted"] == 1

    def test_folder_to_sheet_music(self, quirky_file, tmp_path):
        report = batch_convert([quirky_file], "PDF", tmp_path / "pdfs")
        assert report["target_format"] == "pdf"
        assert report["files"][0]["output"].endswith(".pdf")

    def test_empty_folder(self, tmp_path):
        empty = tmp_path / "empty"
        empty.mkdir()
        with pytest.raises(ScoreError, match="No score files"):
            batch_convert([empty], "abc")


def test_describe_formats_and_repairs():
    from scoreproof import formats, musicxml

    described = formats.describe_formats()
    assert [f["format"] for f in described["convert"]][:2] == ["musicxml", "mxl"]
    assert "Sibelius" in described["not_supported"]
    repairs = musicxml.describe_repairs()
    assert len(repairs) == len(musicxml._RULES)
    assert all(r["description"] for r in repairs)


@pytest.mark.parametrize(
    "transpose",
    [
        "<transpose><diatonic>-1</diatonic><chromatic>-2</chromatic></transpose>",
        "<transpose><diatonic>0</diatonic><chromatic>0</chromatic>"
        "<octave-change>-1</octave-change></transpose>",
    ],
    ids=["clarinet", "guitar"],
)
@pytest.mark.parametrize("target", ["abc", "mei", "midi", "humdrum", "mxl"])
def test_transposing_instruments_keep_their_sound(tmp_path, transpose, target):
    """Formats that drop transpositions get concert pitch, not wrong notes."""
    path = tmp_path / "part.musicxml"
    path.write_text(score([attributes(extra=transpose) + note(), note("D")]))
    result = convert(path, to=target)
    assert result.confidence == "exact"
    if FORMATS[target].concert_pitch:
        assert "transposed_parts" in result.expected_losses


class TestBarDifferences:
    def _part(self, *elements):
        import music21

        return music21.stream.Part([music21.stream.Measure(list(elements))])

    def test_stops_at_the_limit(self, chorale):
        a = load(chorale).stream
        b = a.__deepcopy__()
        for n in b.recurse().notes:
            n.transpose(1, inPlace=True)
        assert len(fidelity.bar_differences(a, b, limit=3)) == 3

    def test_grace_notes_are_ignored_and_drums_are_named(self):
        import music21

        grace = music21.note.Note("C4").getGrace()
        snare = music21.note.Unpitched(displayName="C5", quarterLength=4)
        a = self._part(grace, music21.note.Note("E4", quarterLength=4))
        b = self._part(snare)
        change = fidelity.bar_differences(a, b)[0]
        assert change["only_in_a"] == ["E4, 4 quarters long, 0 quarters into the bar"]
        assert change["only_in_b"] == [
            "unpitched C5, 4 quarters long, 0 quarters into the bar"
        ]


def test_chord_symbols_are_dropped_not_played():
    """Without this, music21's MIDI writer adds chords nobody wrote."""
    import music21

    from scoreproof.formats import Format
    from scoreproof.io import _prepared_for

    measure = music21.stream.Measure(
        [music21.harmony.ChordSymbol("C7"), music21.note.Note("E4", quarterLength=4)]
    )
    stream = music21.stream.Score([music21.stream.Part([measure])])
    keeps_transposition = Format("x", (".x",), "musicxml", "test", ("chord_symbols",))
    prepared = _prepared_for(stream, keeps_transposition)
    assert not prepared.recurse().getElementsByClass("ChordSymbol")
    assert stream.recurse().getElementsByClass("ChordSymbol")
    midi = _prepared_for(stream, FORMATS["midi"])
    assert len(midi.recurse().notes) == 1


def test_convert_any_makes_sheet_music_or_scores(quirky_file, tmp_path):
    from scoreproof.convert import convert_any

    assert convert_any(quirky_file, tmp_path / "out.png")["output"].endswith(".png")
    assert convert_any(quirky_file, to="abc")["target_format"] == "abc"


def test_malformed_repeats_play_once_in_midi(tmp_path):
    """A repeat that starts but never ends (an excerpt cut mid-repeat) made
    music21's MIDI writer fail the whole conversion."""
    from music21 import bar, converter

    piece = converter.parse("tinyNotation: 4/4 c4 d e f g a b c' c' b a g")
    piece.getElementsByClass("Measure")[1].leftBarline = bar.Repeat("start")
    source = tmp_path / "cut.musicxml"
    piece.write("musicxml", fp=str(source))
    result = convert(source, to="midi")
    assert result.confidence == "exact"
    assert result.result["notes"] == 12


def test_abc_tunes_get_a_clef(tmp_path):
    # converter21 reads ABC without clefs, so sheet music had none.
    tune = tmp_path / "tune.abc"
    tune.write_text("X:1\nT:Low Tune\nM:4/4\nL:1/4\nK:C\nC, D, E, F,|G,4|]\n")
    clefs = load(tune).stream.recurse().getElementsByClass("Clef")
    assert [type(c).__name__ for c in clefs] == ["BassClef"]
