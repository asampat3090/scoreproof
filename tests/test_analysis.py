"""Tests for inspect, validate and compare."""

import pytest
from score_xml import attributes, note, score

from scoreproof import analysis, load
from scoreproof.errors import ScoreError


class TestInspect:
    def test_summary_of_a_real_chorale(self, chorale_file):
        info = analysis.inspect(chorale_file, bars=None)
        assert info["format"] == "mxl"
        assert info["bars"] == 10
        assert info["notes"] == 163
        assert info["key_signature"] == "F# minor"
        assert info["estimated_key"]["key"] == "F# minor"
        assert info["time_signatures"] == [{"bar": 0, "meter": "4/4"}]
        assert [p["name"] for p in info["parts"]] == [
            "Soprano",
            "Alto",
            "Tenor",
            "Bass",
        ]
        assert info["parts"][3]["range"] == "F#2-D4"
        assert "excerpt" not in info

    def test_metadata_and_tempo(self, melody_file):
        info = analysis.inspect(melody_file)
        assert info["title"] == "Little Tune"
        assert info["tempos"] == [{"bar": 1, "bpm": 120, "text": ""}]
        assert info["parts"][0]["instrument"] == "Flute"

    def test_excerpt_is_abc_for_the_requested_bars_and_parts(self, chorale_file):
        excerpt = analysis.inspect(chorale_file, bars="1-2", parts=["Bass"])["excerpt"]
        assert excerpt["bars"] == "1-2"
        assert excerpt["parts"] == ["Bass"]
        assert "K:F#min" in excerpt["abc"]
        assert 'nm="Bass"' in excerpt["abc"]
        assert 'nm="Soprano"' not in excerpt["abc"]

    def test_excerpt_is_capped(self, chorale_file, monkeypatch):
        monkeypatch.setattr(analysis, "MAX_EXCERPT_BARS", 3)
        assert analysis.inspect(chorale_file, bars="2-9")["excerpt"]["bars"] == "2-4"

    def test_parts_by_number_and_comma_list(self, chorale_file):
        stream = load(chorale_file).stream
        chosen = analysis.select_parts(stream, "1, alto")
        assert [p.partName for p in chosen] == ["Soprano", "Alto"]

    def test_unknown_part_lists_the_parts(self, chorale_file):
        with pytest.raises(ScoreError, match="Parts: 1 Soprano, 2 Alto"):
            analysis.inspect(chorale_file, parts=["Viola"])

    def test_score_without_notes_or_key(self, tmp_path):
        path = tmp_path / "rests.musicxml"
        rest = "<note><rest/><duration>4</duration><voice>1</voice></note>"
        path.write_text(score([attributes() + rest]))
        info = analysis.inspect(path, bars=None)
        assert info["key_signature"] == ""
        assert info["estimated_key"] == {}
        assert "range" not in info["parts"][0]

    def test_key_estimate_failure_is_skipped(self, melody_file, monkeypatch):
        import music21

        def broken(self, *args, **kwargs):
            raise ValueError("no key")

        monkeypatch.setattr(music21.stream.Score, "analyze", broken)
        assert analysis.inspect(melody_file, bars=None)["estimated_key"] == {}

    def test_bare_stream_and_missing_metadata(self):
        import music21

        part = music21.stream.Part([music21.stream.Measure([music21.note.Note()])])
        part.partName = "Solo"
        assert analysis.all_parts(part) == [part]
        assert analysis._metadata(part, "title") == ""
        excerpt = analysis.excerpt(part, "1")
        assert excerpt["parts"] == ["Solo"]

    def test_subset_without_bars_copies_whole_parts(self, chorale_file):
        stream = load(chorale_file).stream
        piece, first, last = analysis.subset(stream, parts=["2"])
        assert (first, last) == (1, 10)
        assert len(piece.parts) == 1
        assert piece.metadata is not stream.metadata

    def test_key_names_use_b_for_flat(self):
        assert analysis.key_name("B- major") == "Bb major"


class TestValidate:
    def test_clean_score(self, chorale_file):
        assert analysis.validate(chorale_file) == {
            "file": str(chorale_file),
            "status": "clean",
            "issues": [],
        }

    def test_repairs_are_reported(self, tmp_path):
        path = tmp_path / "guitar.musicxml"
        extra = "<transpose><octave-change>-1</octave-change></transpose>"
        path.write_text(score([attributes(extra=extra) + note()]))
        report = analysis.validate(path)
        assert report["status"] == "repaired"
        assert report["issues"][0]["check"] == "complete_transposes"

    def test_irregular_bar_and_out_of_range_note(self, tmp_path):
        path = tmp_path / "flute.musicxml"
        bars = [attributes() + note("C"), note("C", 2, 2, "half"), note("C", 4)]
        part = (
            "<score-instrument id='P1-I1'><instrument-name>Flute</instrument-name>"
            "</score-instrument>"
        )
        path.write_text(score(bars, name="Flute", score_part_extra=part))
        report = analysis.validate(path)
        checks = [i["check"] for i in report["issues"]]
        assert report["status"] == "warnings"
        assert "irregular_bar" in checks
        out_of_range = next(i for i in report["issues"] if i["check"] == "out_of_range")
        assert "C2 is below the lowest Flute note (C4)" in out_of_range["detail"]
        assert out_of_range["detail"].endswith("bars 2")

    def test_reader_warnings_are_info(self, chorale_file, monkeypatch):
        from scoreproof import io as score_io

        original = score_io.load

        def with_warning(*args, **kwargs):
            loaded = original(*args, **kwargs)
            loaded.warnings = ["odd thing"]
            return loaded

        monkeypatch.setattr(analysis, "load", with_warning)
        issue = analysis.validate(chorale_file)["issues"][0]
        assert issue == {
            "severity": "info",
            "check": "reader_warning",
            "detail": "odd thing",
        }


class TestCompare:
    def test_identical_across_formats(self, chorale_file):
        from scoreproof import convert

        midi = convert(chorale_file, to="midi").output
        report = analysis.compare(chorale_file, midi)
        assert report["identical_notes"] is True
        assert report["changed_bars"] == []

    def test_changed_note_is_located(self, melody_file, tmp_path):
        edited = tmp_path / "edited.musicxml"
        edited.write_text(
            melody_file.read_text().replace("<step>E</step>", "<step>F</step>")
        )
        report = analysis.compare(melody_file, edited)
        assert report["identical_notes"] is False
        assert report["changed_bars"] == [
            {
                "part": "Flute",
                "bar": "3",
                "only_in_a": ["E4, 4 quarters long, 0 quarters into the bar"],
                "only_in_b": ["F4, 4 quarters long, 0 quarters into the bar"],
            }
        ]


class TestEdgeCases:
    def test_empty_tempo_marks_are_skipped(self):
        import music21

        part = music21.stream.Part(
            [
                music21.stream.Measure(
                    [music21.tempo.MetronomeMark(), music21.note.Note()]
                )
            ]
        )
        assert analysis._tempos(part) == []

    def test_note_above_the_highest_piano_key(self, tmp_path):
        path = tmp_path / "piano.musicxml"
        part = (
            "<score-instrument id='P1-I1'><instrument-name>Piano</instrument-name>"
            "</score-instrument>"
        )
        path.write_text(score([attributes() + note("D", 8)], score_part_extra=part))
        issue = analysis.validate(path)["issues"][0]
        assert issue["detail"] == "D8 is above the highest Piano note (C8) in bars 1"

    def test_instruments_without_a_known_range(self):
        import music21

        bare = music21.stream.Part([music21.stream.Measure([music21.note.Note()])])
        assert analysis._instrument_with_range(bare) is None
        voice = music21.instrument.Instrument()
        voice.instrumentName = "Soprano"
        bare.insert(0, voice)
        assert analysis._instrument_with_range(bare) is None
