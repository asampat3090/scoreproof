"""Tests for rendering sheet music and audio."""

import wave

import pytest

from scoreproof import convert, render
from scoreproof.errors import ScoreError


def test_svg_from_musicxml(chorale_file):
    result = render.render(chorale_file)
    assert result["pages"] == 1
    assert result["output"].endswith("chorale.svg")
    svg = open(result["output"]).read()
    assert svg.lstrip().startswith("<svg") or "<svg" in svg[:500]


def test_svg_from_midi_goes_through_musicxml(chorale_file):
    midi = convert(chorale_file, to="midi").output
    assert render.render(midi, "SVG")["pages"] >= 1


def test_long_scores_render_one_file_per_page(chorale_file, monkeypatch):
    import verovio

    real = verovio.toolkit

    class ThreePages:
        def __init__(self, init_font=True):
            self.toolkit = real(init_font)

        def __getattr__(self, name):
            return getattr(self.toolkit, name)

        def getPageCount(self):
            return 3

        def renderToSVG(self, page):
            return f"<svg>page {page}</svg>"

    monkeypatch.setattr(verovio, "toolkit", ThreePages)
    result = render.render(chorale_file)
    assert [f.rsplit("/", 1)[1] for f in result["files"]] == [
        "chorale.svg",
        "chorale-page2.svg",
        "chorale-page3.svg",
    ]


def test_engraving_failure(chorale_file, monkeypatch):
    import verovio

    class Refuses:
        def __init__(self, init_font=True):
            pass

        def setResourcePath(self, path):
            pass

        def setOptions(self, options):
            pass

        def loadData(self, data):
            return False

    monkeypatch.setattr(verovio, "toolkit", Refuses)
    with pytest.raises(ScoreError, match="could not engrave"):
        render.render(chorale_file)


def test_wav_preview(chorale_file):
    result = render.render(chorale_file, "wav")
    with wave.open(result["output"]) as audio:
        assert audio.getframerate() == render.SAMPLE_RATE
        assert audio.getnchannels() == 1
        seconds = audio.getnframes() / audio.getframerate()
    assert result["seconds"] == round(seconds, 1)
    assert 20 < seconds < 30


def test_silent_score_cannot_be_played(tmp_path):
    from score_xml import attributes, score

    path = tmp_path / "rests.musicxml"
    rest = "<note><rest/><duration>4</duration><voice>1</voice></note>"
    path.write_text(score([attributes() + rest]))
    with pytest.raises(ScoreError, match="no pitched notes"):
        render.render(path, "wav")


def test_unknown_render_target(chorale_file):
    with pytest.raises(ScoreError, match="Choose from: svg, wav"):
        render.render(chorale_file, "pdf")
