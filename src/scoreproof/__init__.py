"""Read, repair and convert music notation files (MusicXML, MIDI, ABC, MEI, kern).

Requires the ``score`` extra: ``pip install songchain[score]``. Does not
import torch.
"""

from songchain.score.convert import ConversionResult, convert
from songchain.score.formats import FORMATS, Format, detect_format, get_format
from songchain.score.io import Score, load

__all__ = [
    "FORMATS",
    "ConversionResult",
    "Format",
    "Score",
    "convert",
    "detect_format",
    "get_format",
    "load",
]
