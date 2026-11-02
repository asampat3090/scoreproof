"""Tests for input and output path handling."""

import pytest

from scoreproof.errors import ScoreError
from scoreproof.paths import input_path, output_path, parse_bars


def test_input_path_expands_and_resolves(tmp_path, monkeypatch):
    (tmp_path / "a.mid").write_bytes(b"")
    monkeypatch.setenv("HOME", str(tmp_path))
    assert input_path("~/a.mid") == (tmp_path / "a.mid").resolve()


def test_missing_input(tmp_path):
    with pytest.raises(ScoreError, match="File not found"):
        input_path(tmp_path / "missing.mid")


class TestOutputPath:
    def test_default_sits_next_to_source(self, tmp_path):
        source = tmp_path / "song.musicxml"
        assert output_path(source, ".mid") == tmp_path / "song.mid"
        assert output_path(source, ".mid", suffix="-d") == tmp_path / "song-d.mid"

    def test_default_never_overwrites(self, tmp_path):
        source = tmp_path / "song.musicxml"
        (tmp_path / "song.mid").write_bytes(b"")
        (tmp_path / "song-2.mid").write_bytes(b"")
        assert output_path(source, ".mid") == tmp_path / "song-3.mid"

    def test_explicit_file_is_used_as_given(self, tmp_path):
        target = tmp_path / "keep.mid"
        target.write_bytes(b"")
        assert output_path(tmp_path / "song.xml", ".mid", target) == target

    def test_folder_gets_an_unused_name(self, tmp_path):
        out = tmp_path / "out"
        out.mkdir()
        (out / "song.mid").write_bytes(b"")
        assert output_path(tmp_path / "song.xml", ".mid", out) == out / "song-2.mid"


@pytest.mark.parametrize(
    "spec, expected",
    [
        ("5", (5, 5)),
        ("1-8", (1, 8)),
        ("9-", (9, 20)),
        ("-4", (1, 4)),
        (" 2 - 3 ", (2, 3)),
    ],
)
def test_parse_bars(spec, expected):
    assert parse_bars(spec, total=20) == expected


@pytest.mark.parametrize(
    "spec, message", [("x", "must look like"), ("8-2", "ends before")]
)
def test_parse_bars_rejects_nonsense(spec, message):
    with pytest.raises(ScoreError, match=message):
        parse_bars(spec, total=20)
