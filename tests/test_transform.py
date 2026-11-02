"""Tests for transposing and extracting."""

from pathlib import Path

import pytest

from scoreproof import load, transform
from scoreproof.errors import ScoreError


class TestTranspose:
    @pytest.mark.parametrize(
        "kwargs, label, key, name",
        [
            ({"by": "M2"}, "M2", "G# minor", "chorale-gsharp-minor.mxl"),
            ({"by": "1"}, "m2", "G minor", "chorale-g-minor.mxl"),
            ({"by": "-14"}, "M-9", "E minor", "chorale-e-minor.mxl"),
            ({"to_key": "Bbm"}, "d4", "Bb minor", "chorale-bb-minor.mxl"),
            ({"to_key": "e minor"}, "M-2", "E minor", "chorale-e-minor.mxl"),
        ],
    )
    def test_transpositions(self, chorale_file, kwargs, label, key, name):
        result = transform.transpose(chorale_file, **kwargs)
        assert result["transposition"] == label
        assert (result["from_key"], result["to_key"]) == ("F# minor", key)
        assert Path(result["output"]).name == name
        assert result["notes"] == 163
        assert "note" not in result

    def test_target_key_in_other_mode_explains_itself(self, chorale_file):
        result = transform.transpose(chorale_file, to_key="C")
        assert result["to_key"] == "C minor"
        assert "mode was kept" in result["note"]

    def test_semitones_without_a_key_signature(self, tmp_path):
        from score_xml import attributes, note, score

        path = tmp_path / "plain.musicxml"
        path.write_text(score([attributes() + note("C")]))
        result = transform.transpose(path, by="3")
        assert result["transposition"] == "m3"
        notes = load(result["output"]).stream.recurse().notes
        assert [n.nameWithOctave for n in notes] == ["E-4"]

    def test_concert_pitch_for_a_clarinet(self, tmp_path):
        from score_xml import attributes, note, score

        extra = (
            "<transpose><diatonic>-1</diatonic><chromatic>-2</chromatic></transpose>"
        )
        path = tmp_path / "clarinet.musicxml"
        path.write_text(score([attributes(extra=extra) + note("D")], name="Clarinet"))
        result = transform.transpose(path, pitch="concert", to="abc")
        assert result["output"].endswith("clarinet-concert.abc")
        assert result["transposition"] == "concert pitch"
        written = transform.transpose(path, pitch="written")
        assert written["output"].endswith("clarinet-written.musicxml")

    @pytest.mark.parametrize(
        "kwargs, message",
        [
            ({}, "exactly one"),
            ({"by": "M2", "to_key": "D"}, "exactly one"),
            ({"by": "huge"}, "Not an interval"),
            ({"to_key": "H major"}, "Not a key"),
            ({"to_key": "D dorian"}, "major or minor"),
            ({"to_key": "  "}, "Empty key"),
            ({"pitch": "sideways"}, "concert' or 'written"),
        ],
    )
    def test_bad_requests(self, chorale_file, kwargs, message):
        with pytest.raises(ScoreError, match=message):
            transform.transpose(chorale_file, **kwargs)

    def test_target_key_needs_a_key_signature(self, tmp_path):
        from score_xml import attributes, note, score

        path = tmp_path / "plain.musicxml"
        path.write_text(score([attributes() + note()]))
        with pytest.raises(ScoreError, match="no key signature"):
            transform.transpose(path, to_key="D")


class TestExtract:
    def test_parts_and_bars(self, chorale_file):
        files = transform.extract(chorale_file, parts="Soprano,Bass", bars="1-4")[
            "files"
        ]
        assert len(files) == 1
        assert files[0]["parts"] == ["Soprano", "Bass"]
        assert files[0]["bars"] == "1-4"
        assert files[0]["output"].endswith("chorale-soprano-bass-bars-1-4.mxl")
        assert len(load(files[0]["output"]).stream.parts) == 2

    def test_each_part_makes_one_file_per_player(self, chorale_file):
        files = transform.extract(chorale_file, each_part=True, to="musicxml")["files"]
        assert [f["parts"] for f in files] == [
            ["Soprano"],
            ["Alto"],
            ["Tenor"],
            ["Bass"],
        ]
        assert files[0]["output"].endswith("chorale-soprano.musicxml")
        assert all(f["bars"] == "all" for f in files)

    def test_each_part_respects_the_part_choice(self, chorale_file):
        files = transform.extract(chorale_file, parts=["2", "4"], each_part=True)[
            "files"
        ]
        assert [f["parts"] for f in files] == [["Alto"], ["Bass"]]

    def test_whole_score_copy_is_named_extract(self, chorale_file):
        files = transform.extract(chorale_file)["files"]
        assert files[0]["output"].endswith("chorale-extract.mxl")


def test_key_moves_take_the_shorter_way(melody_file):
    down = transform.transpose(melody_file, to_key="B")
    assert (down["to_key"], down["transposition"]) == ("B major", "m-2")
    up = transform.transpose(down["output"], to_key="C")
    assert (up["to_key"], up["transposition"]) == ("C major", "m2")
