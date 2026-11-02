"""Tests for the scoreproof command."""

import json
import runpy

import pytest

from scoreproof import __version__, cli


def run(capsys, *args):
    code = cli.main([str(a) for a in args])
    out, err = capsys.readouterr()
    return code, out, err


@pytest.mark.parametrize("flag", ["--version", "-V"])
def test_version_flag(capsys, flag):
    with pytest.raises(SystemExit) as exit_info:
        cli.main([flag])
    assert exit_info.value.code == 0
    assert capsys.readouterr().out.strip() == __version__


def test_no_command_prints_help(capsys):
    code, out, _ = run(capsys)
    assert code == 0
    assert "Convert any score" in out
    assert "scoreproof mcp" in out


def test_convert_with_output_name(capsys, chorale_file, tmp_path):
    code, out, _ = run(capsys, "convert", chorale_file, tmp_path / "song.mid")
    assert code == 0
    assert "confidence: exact" in out
    assert (tmp_path / "song.mid").exists()


def test_json_output(capsys, chorale_file):
    code, out, _ = run(capsys, "convert", chorale_file, "--to", "abc", "--json")
    assert json.loads(out)["target_format"] == "abc"


def test_errors_go_to_stderr_with_exit_code_1(capsys, tmp_path):
    code, out, err = run(capsys, "convert", tmp_path / "nope.xml", "--to", "abc")
    assert code == 1
    assert out == ""
    assert err.startswith("error: File not found")


@pytest.mark.parametrize(
    "args, expected",
    [
        (["inspect", "{f}", "--bars", "1-2", "--parts", "Bass"], 'nm="Bass"'),
        (["inspect", "{f}", "--bars", ""], "key signature: F# minor"),
        (["validate", "{f}"], "status: clean"),
        (["transpose", "{f}", "--interval", "M2"], "to key: G# minor"),
        (["transpose", "{f}", "--concert"], "transposition: concert pitch"),
        (["extract", "{f}", "--each-part"], "parts:"),
        (["render", "{f}", "--to", "svg"], "pages: 1"),
        (["formats"], "- format: musicxml"),
    ],
)
def test_commands(capsys, chorale_file, args, expected):
    code, out, _ = run(capsys, *[a.format(f=chorale_file) for a in args])
    assert code == 0
    assert expected in out


def test_batch_and_compare(capsys, chorale_file, tmp_path):
    out_dir = tmp_path / "out"
    code, out, _ = run(capsys, "batch", tmp_path, "--to", "midi", "-o", out_dir)
    assert code == 0 and "converted: 1" in out
    code, out, _ = run(capsys, "compare", chorale_file, out_dir / "chorale.mid")
    assert "identical notes: True" in out


def test_mcp_command_starts_the_server(capsys, monkeypatch):
    from scoreproof import server

    started = []
    monkeypatch.setattr(server, "main", lambda: started.append(True))
    assert cli.main(["mcp"]) == 0
    assert started == [True]


def test_show_formats_nested_results(capsys):
    cli.show(
        {
            "empty": "",
            "items": [{"bar": "3", "only_in_a": ["E4"]}, "plain"],
            "abc": "X:1\nK:C",
        }
    )
    assert capsys.readouterr().out.splitlines() == [
        "items:",
        "  - bar: 3",
        "    only in a:",
        "      - E4",
        "  - plain",
        "abc:",
        "  X:1",
        "  K:C",
    ]


def test_runs_as_module(capsys, monkeypatch):
    monkeypatch.setattr("sys.argv", ["scoreproof", "formats", "--json"])
    with pytest.raises(SystemExit) as exit_info:
        runpy.run_module("scoreproof", run_name="__main__")
    assert exit_info.value.code == 0
    assert "convert" in json.loads(capsys.readouterr().out)
