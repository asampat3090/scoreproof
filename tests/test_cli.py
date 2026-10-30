"""Tests for the placeholder command-line entry point."""

import runpy

import pytest

from scoreproof import __version__, cli


@pytest.mark.parametrize("flag", ["--version", "-V"])
def test_version_flag(capsys, flag):
    assert cli.main([flag]) == 0
    assert capsys.readouterr().out.strip() == __version__


def test_default_message(capsys, monkeypatch):
    monkeypatch.setattr("sys.argv", ["scoreproof"])
    assert cli.main() == 0
    assert "in development" in capsys.readouterr().out


def test_runs_as_module(capsys, monkeypatch):
    monkeypatch.setattr("sys.argv", ["scoreproof", "--version"])
    with pytest.raises(SystemExit) as exit_info:
        runpy.run_module("scoreproof", run_name="__main__")
    assert exit_info.value.code == 0
    assert capsys.readouterr().out.strip() == __version__
