"""Render a score to sheet music (SVG) or audio (WAV) for a quick preview."""

import tempfile
import wave
from pathlib import Path

from scoreproof import musicxml
from scoreproof.errors import ScoreError
from scoreproof.io import load
from scoreproof.paths import input_path, output_path

RENDER_FORMATS = {
    "svg": "Sheet music, one SVG file per page (engraved by Verovio)",
    "wav": "Audio preview with a simple built-in synth (no SoundFont needed)",
}
SAMPLE_RATE = 22050


def render(path, to="svg", output=None) -> dict:
    """Render ``path`` to sheet music or audio next to the source."""
    to = to.lower().lstrip(".")
    if to not in RENDER_FORMATS:
        raise ScoreError(
            f"Cannot render to {to!r}. Choose from: {', '.join(RENDER_FORMATS)}"
        )
    source = input_path(path)
    return _svg(source, output) if to == "svg" else _wav(source, output)


def _svg(source: Path, output) -> dict:
    import verovio

    # Point Verovio at its bundled fonts: its default lookup fails on the
    # worker threads an MCP server runs tools on.
    toolkit = verovio.toolkit(False)
    toolkit.setResourcePath(str(Path(verovio.__file__).parent / "data"))
    toolkit.setOptions({"adjustPageHeight": True, "footer": "none"})
    if not toolkit.loadData(_musicxml_text(source)):
        raise ScoreError("Verovio could not engrave this score")
    pages = toolkit.getPageCount()
    first = output_path(source, ".svg", output)
    files = []
    for page in range(1, pages + 1):
        target = first if page == 1 else _page_path(first, page)
        target.write_text(toolkit.renderToSVG(page))
        files.append(str(target))
    return {"output": files[0], "pages": pages, "files": files}


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
