"""The score formats scoreproof can read and write, and what each one loses."""

from dataclasses import dataclass
from pathlib import Path

from scoreproof.errors import ScoreError


@dataclass(frozen=True)
class Format:
    name: str
    extensions: tuple
    music21_format: str
    description: str
    # Notation features the format cannot represent, so a conversion into it
    # is expected to drop them. Measured losses are reported separately.
    cannot_hold: tuple = ()
    # Stores the music as performed (repeats written out), not as notated.
    performed: bool = False
    # The format (or its writer) drops instrument transpositions, so scores
    # are written at concert pitch to keep every note sounding right.
    concert_pitch: bool = False

    @property
    def extension(self) -> str:
        return self.extensions[0]


_MIDI_LOSSES = (
    "lyrics",
    "chord_symbols",
    "dynamics",
    "articulations",
    "slurs",
    "pitch_spelling",
    "layout",
    "transposed_parts",
)

FORMATS = {
    f.name: f
    for f in (
        Format(
            "musicxml",
            (".musicxml", ".xml"),
            "musicxml",
            "MusicXML, the interchange format every notation app exports",
        ),
        Format("mxl", (".mxl",), "musicxml", "Compressed MusicXML"),
        Format(
            "midi",
            (".mid", ".midi"),
            "midi",
            "Standard MIDI file (performance data, no notation)",
            _MIDI_LOSSES,
            performed=True,
            concert_pitch=True,
        ),
        Format(
            "abc",
            (".abc",),
            "abc",
            "ABC notation (compact text)",
            ("layout", "transposed_parts"),
            concert_pitch=True,
        ),
        Format(
            "mei",
            (".mei",),
            "mei",
            "Music Encoding Initiative XML",
            ("transposed_parts",),
            concert_pitch=True,
        ),
        Format(
            "humdrum",
            (".krn", ".kern"),
            "humdrum",
            "Humdrum **kern (analysis-oriented text)",
            ("layout",),
        ),
    )
}


def get_format(name: str) -> Format:
    try:
        return FORMATS[name.lower().lstrip(".")]
    except KeyError:
        by_ext = {ext: f for f in FORMATS.values() for ext in f.extensions}
        fmt = by_ext.get("." + name.lower().lstrip("."))
        if fmt is None:
            raise ScoreError(
                f"Unknown format {name!r}. Supported: {', '.join(FORMATS)}"
            ) from None
        return fmt


def detect_format(path) -> Format:
    """Pick a format from the file extension, sniffing ambiguous .xml files."""
    path = Path(path)
    suffix = path.suffix.lower()
    if suffix == ".xml" and path.exists():
        with open(path, "rb") as f:
            head = f.read(4096)
        if b"<mei" in head:
            return FORMATS["mei"]
    if not suffix:
        raise ScoreError(f"Cannot tell the format of {path.name!r}: no extension")
    return get_format(suffix)


def describe_formats() -> dict:
    """Every format with its extensions and what converting into it loses."""
    from scoreproof.render import RENDER_FORMATS

    return {
        "convert": [
            {
                "format": f.name,
                "extensions": list(f.extensions),
                "description": f.description,
                "cannot_hold": list(f.cannot_hold),
            }
            for f in FORMATS.values()
        ],
        "render": [
            {"format": name, "description": text}
            for name, text in RENDER_FORMATS.items()
        ],
        "not_supported": (
            "Native Sibelius (.sib), Dorico (.dorico), Finale (.musx) and "
            "MuseScore (.mscz) files: export MusicXML from the app first."
        ),
    }
