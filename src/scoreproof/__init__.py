"""Scoreproof: convert any score, prove nothing got lost.

Reads, repairs and converts music notation files (MusicXML, MIDI, ABC, MEI,
Humdrum) and checks every conversion note by note.
"""

from scoreproof.convert import ConversionResult, convert
from scoreproof.errors import ScoreError
from scoreproof.formats import FORMATS, Format, detect_format, get_format
from scoreproof.io import Score, load

__version__ = "0.0.1"

__all__ = [
    "FORMATS",
    "ConversionResult",
    "Format",
    "Score",
    "ScoreError",
    "convert",
    "detect_format",
    "get_format",
    "load",
]
