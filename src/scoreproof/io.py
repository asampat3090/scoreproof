"""Load scores from any supported format and save them to another."""

import warnings
from dataclasses import dataclass, field
from pathlib import Path

from scoreproof import musicxml
from scoreproof.errors import ScoreError
from scoreproof.formats import Format, detect_format, get_format

_registered = False


def _music21():
    """Import music21 with converter21's ABC, MEI and Humdrum support."""
    global _registered
    import music21

    if not _registered:
        import converter21

        converter21.register()
        _registered = True
    return music21


@dataclass
class Score:
    """A parsed score plus what was repaired or noticed while reading it."""

    stream: object
    format: Format
    source: Path = None
    fixes: list = field(default_factory=list)
    warnings: list = field(default_factory=list)
    software: str = ""
    irregular_bars: list = field(default_factory=list)

    def save(self, path, format=None) -> Path:
        """Write the score to ``path`` in ``format`` (default: by extension)."""
        path = Path(path)
        fmt = get_format(format) if format else detect_format(path)
        subformat = "mxl" if fmt.name == "mxl" else fmt.music21_format
        stream = _prepared_for(self.stream, fmt)
        try:
            with warnings.catch_warnings(record=True):
                warnings.simplefilter("always")
                written = stream.write(subformat, fp=str(path))
        except Exception as exc:  # music21 raises many unrelated types
            raise ScoreError(
                f"Could not write {fmt.name}: {type(exc).__name__}: {exc}"
            ) from exc
        return Path(written)


def _prepared_for(stream, fmt):
    """The stream adjusted so a writer cannot silently change the music.

    - Formats that drop transpositions get concert pitch with the
      transpositions removed, so the writer cannot shift notes back.
    - Formats that cannot hold chord symbols lose them here; music21's MIDI
      writer would otherwise play them as extra chords nobody wrote.
    """
    if not fmt.concert_pitch and "chord_symbols" not in fmt.cannot_hold:
        return stream
    if fmt.concert_pitch:
        stream = stream.toSoundingPitch(inPlace=False)
        for instrument in stream.recurse().getElementsByClass("Instrument"):
            instrument.transposition = None
    else:
        stream = stream.__deepcopy__()
    if "chord_symbols" in fmt.cannot_hold:
        for symbol in list(stream.recurse().getElementsByClass("ChordSymbol")):
            symbol.activeSite.remove(symbol)
    return stream


def load(path, format=None) -> Score:
    """Read a score file, repairing known exporter quirks in MusicXML."""
    path = Path(path)
    if not path.exists():
        raise ScoreError(f"File not found: {path}")
    fmt = get_format(format) if format else detect_format(path)
    music21 = _music21()
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        if fmt.music21_format == "musicxml":
            score = _load_musicxml(music21, path.read_bytes(), fmt)
        else:
            score = Score(_parse(music21, str(path), fmt), fmt)
    score.source = path
    score.warnings = sorted({str(w.message) for w in caught})
    return score


def _load_musicxml(music21, data: bytes, fmt: Format) -> Score:
    normalized = musicxml.normalize(data)
    stream = _parse(music21, normalized.xml.decode("utf-8"), fmt)
    return Score(
        stream,
        fmt,
        fixes=normalized.fixes,
        software=normalized.software,
        irregular_bars=normalized.irregular_bars,
    )


def _parse(music21, source, fmt: Format):
    try:
        if fmt.music21_format == "musicxml":
            stream = music21.converter.parseData(source, format="musicxml")
        else:
            stream = music21.converter.parse(
                source, format=fmt.music21_format, forceSource=True
            )
    except Exception as exc:  # music21 raises many unrelated types
        raise ScoreError(
            f"Could not read {fmt.name}: {type(exc).__name__}: {exc}"
        ) from exc
    if isinstance(stream, music21.stream.Opus):
        stream = stream.scores[0]
    return stream
