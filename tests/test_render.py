"""Tests for rendering sheet music and audio."""

import wave

import pytest

from scoreproof import convert, render
from scoreproof.errors import ScoreError


def test_pdf_is_the_default(chorale_file):
    from pypdf import PdfReader

    result = render.render(chorale_file)
    assert result["output"].endswith("chorale.pdf")
    assert result == {"output": result["output"], "pages": 1, "page_size": "letter"}
    page = PdfReader(result["output"]).pages[0]
    assert [round(float(v)) for v in page.mediabox[2:]] == [612, 792]
    # Vector output: the music is drawn, not embedded as an image.
    assert not page.images


def test_a4_pdf(chorale_file):
    from pypdf import PdfReader

    result = render.render(chorale_file, "pdf", page_size="a4")
    page = PdfReader(result["output"]).pages[0]
    assert [round(float(v)) for v in page.mediabox[2:]] == [595, 842]


def test_png_pages(chorale_file):
    result = render.render(chorale_file, "png")
    assert result["files"] == [result["output"]]
    data = open(result["output"], "rb").read()
    assert data[:8] == b"\x89PNG\r\n\x1a\n"
    width = int.from_bytes(data[16:20], "big")
    assert width == round(8.5 * render.PNG_PPI)


def test_tempo_marks_use_the_music_font(chorale_file):
    """The metronome note is a music-font glyph set as text: without the
    unpacked font Typst would print a missing-glyph box."""
    font = render._text_font()
    assert font[:4] in (b"OTTO", b"\x00\x01\x00\x00")


def test_svg_from_musicxml(chorale_file):
    result = render.render(chorale_file, "svg")
    assert result["pages"] == 1
    assert result["output"].endswith("chorale.svg")
    svg = open(result["output"]).read()
    assert svg.lstrip().startswith("<svg") or "<svg" in svg[:500]


def test_svg_from_midi_goes_through_musicxml(chorale_file):
    midi = convert(chorale_file, to="midi").output
    assert render.render(midi, "SVG")["pages"] >= 1


def test_titles_survive_typst():
    svg = '<text><tspan x="5"><title class="labelAttr">title</title>Song</tspan></text>'
    assert render._for_typst(svg) == '<text><tspan x="5">Song</tspan></text>'


def test_png_of_many_pages(chorale_file, monkeypatch):
    import typst

    monkeypatch.setattr(typst, "compile", lambda *a, **k: [b"one", b"two"])
    result = render.render(chorale_file, "png")
    assert [f.rsplit("/", 1)[1] for f in result["files"]] == [
        "chorale.png",
        "chorale-page2.png",
    ]


def test_png_of_one_page_as_bytes(chorale_file, monkeypatch):
    import typst

    monkeypatch.setattr(typst, "compile", lambda *a, **k: b"only")
    assert len(render.render(chorale_file, "png")["files"]) == 1


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
    result = render.render(chorale_file, "svg")
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
    with pytest.raises(ScoreError, match="Choose from: pdf, png, svg, wav"):
        render.render(chorale_file, "jpg")


def test_unknown_page_size(chorale_file):
    with pytest.raises(ScoreError, match="Choose from: letter, a4"):
        render.render(chorale_file, "pdf", page_size="legal")
