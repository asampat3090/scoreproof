"""Render a score to sheet music (PDF, PNG, SVG) or audio (WAV)."""

import base64
import functools
import io
import re
import tempfile
import wave
from pathlib import Path

from scoreproof import musicxml
from scoreproof.errors import ScoreError
from scoreproof.io import load
from scoreproof.paths import input_path, output_path

RENDER_FORMATS = {
    "pdf": "Printable sheet music, all pages in one PDF (engraved by Verovio)",
    "png": "Sheet music images, one PNG per page",
    "svg": "Sheet music for screens, one SVG per page",
    "wav": "Audio preview with a simple built-in synth (no SoundFont needed)",
}
# Page sizes in Verovio units (tenths of a millimetre).
PAGE_SIZES = {"letter": (2159, 2794), "a4": (2100, 2970)}
# Staff size: 40 gives 7 mm staves, the usual size for printed parts.
SCALE = 40
PNG_PPI = 150
SAMPLE_RATE = 22050
MUSIC_FONT = "Leipzig"


def render(path, to="pdf", output=None, page_size="letter") -> dict:
    """Render ``path`` to sheet music or audio next to the source."""
    to = to.lower().lstrip(".")
    if to not in RENDER_FORMATS:
        raise ScoreError(
            f"Cannot render to {to!r}. Choose from: {', '.join(RENDER_FORMATS)}"
        )
    if page_size not in PAGE_SIZES:
        raise ScoreError(
            f"Unknown page size {page_size!r}. Choose from: {', '.join(PAGE_SIZES)}"
        )
    source = input_path(path)
    if to == "wav":
        return _wav(source, output)
    if to == "svg":
        return _svg(source, output)
    return _printed(source, output, to, page_size)


def _engrave(source: Path, options: dict):
    import verovio

    # Point Verovio at its bundled fonts: its default lookup fails on the
    # worker threads an MCP server runs tools on.
    toolkit = verovio.toolkit(False)
    toolkit.setResourcePath(str(_verovio_data()))
    toolkit.setOptions({"font": MUSIC_FONT, "footer": "none", **options})
    if not toolkit.loadData(_musicxml_text(source)):
        raise ScoreError("Verovio could not engrave this score")
    return toolkit


def _verovio_data() -> Path:
    import verovio

    return Path(verovio.__file__).parent / "data"


def _svg(source: Path, output) -> dict:
    toolkit = _engrave(source, {"adjustPageHeight": True})
    first = output_path(source, ".svg", output)
    files = []
    for page in range(1, toolkit.getPageCount() + 1):
        target = first if page == 1 else _page_path(first, page)
        target.write_text(toolkit.renderToSVG(page))
        files.append(str(target))
    return {"output": files[0], "pages": len(files), "files": files}


def _printed(source: Path, output, to: str, page_size: str) -> dict:
    """Lay the score out on real pages and typeset them as PDF or PNG.

    Verovio engraves each page as SVG; Typst turns the pages into a vector
    PDF or images. Both ship as wheels, so nothing needs installing.
    """
    import typst

    width, height = PAGE_SIZES[page_size]
    toolkit = _engrave(
        source,
        {
            "pageWidth": width,
            "pageHeight": height,
            "scale": SCALE,
            "pageMarginTop": 100,
            "pageMarginBottom": 100,
            "pageMarginLeft": 100,
            "pageMarginRight": 100,
        },
    )
    pages = toolkit.getPageCount()
    with tempfile.TemporaryDirectory() as tmp:
        folder = Path(tmp)
        (folder / f"{MUSIC_FONT}.otf").write_bytes(_text_font())
        lines = [
            f"#set page(width: {width / 10}mm, height: {height / 10}mm, margin: 0pt)"
        ]
        for page in range(1, pages + 1):
            (folder / f"{page}.svg").write_text(_for_typst(toolkit.renderToSVG(page)))
            lines.append(f'#page(image("{page}.svg", width: 100%, height: 100%))')
        (folder / "score.typ").write_text("\n".join(lines))
        rendered = typst.compile(
            str(folder / "score.typ"),
            root=str(folder),
            font_paths=[str(folder)],
            format=to,
            ppi=PNG_PPI,
        )
    if to == "pdf":
        target = output_path(source, ".pdf", output)
        target.write_bytes(rendered)
        return {"output": str(target), "pages": pages, "page_size": page_size}
    images = rendered if isinstance(rendered, list) else [rendered]
    first = output_path(source, ".png", output)
    files = []
    for page, image in enumerate(images, 1):
        target = first if page == 1 else _page_path(first, page)
        target.write_bytes(image)
        files.append(str(target))
    return {"output": files[0], "pages": pages, "files": files, "page_size": page_size}


def _for_typst(svg: str) -> str:
    """Drop the tooltip <title> elements Verovio puts inside text: Typst's
    SVG reader then loses the text's position (titles land off the page)."""
    return re.sub(r"<title[^>]*>.*?</title>", "", svg, flags=re.S)


@functools.cache
def _text_font() -> bytes:
    """The music font as OpenType, for symbols set as text (tempo marks).

    Verovio embeds it in its CSS as WOFF2, which Typst cannot read.
    """
    from fontTools.ttLib import TTFont

    css = (_verovio_data() / f"{MUSIC_FONT}.css").read_text()
    data = base64.b64decode(re.search(r"base64,([^)]+)\)", css).group(1))
    font = TTFont(io.BytesIO(data))
    font.flavor = None
    out = io.BytesIO()
    font.save(out)
    return out.getvalue()


def _page_path(first: Path, page: int) -> Path:
    return first.with_name(f"{first.stem}-page{page}{first.suffix}")


def _musicxml_text(source: Path) -> str:
    """MusicXML for Verovio: the repaired original when the input is
    MusicXML (it keeps the layout), otherwise exported from the score."""
    score = load(source)
    if score.format.music21_format == "musicxml":
        return musicxml.normalize(source.read_bytes()).xml.decode("utf-8")
    with tempfile.TemporaryDirectory() as tmp:
        written = score.save(Path(tmp) / "score.musicxml", "musicxml")
        return written.read_text()


def _wav(source: Path, output) -> dict:
    import numpy as np
    import pretty_midi

    score = load(source)
    with tempfile.TemporaryDirectory() as tmp:
        midi = score.save(Path(tmp) / "score.mid", "midi")
        performance = pretty_midi.PrettyMIDI(str(midi))
    audio = performance.synthesize(fs=SAMPLE_RATE, wave=_organ)
    if not audio.size or not np.abs(audio).max():
        raise ScoreError("There are no pitched notes to play")
    samples = (audio / np.abs(audio).max() * 0.8 * 32767).astype("<i2")
    target = output_path(source, ".wav", output)
    with wave.open(str(target), "wb") as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(SAMPLE_RATE)
        out.writeframes(samples.tobytes())
    return {"output": str(target), "seconds": round(len(samples) / SAMPLE_RATE, 1)}


def _organ(phase):
    """A soft organ-like tone: a sine plus two quieter overtones."""
    import numpy as np

    return np.sin(phase) + 0.4 * np.sin(2 * phase) + 0.2 * np.sin(3 * phase)
