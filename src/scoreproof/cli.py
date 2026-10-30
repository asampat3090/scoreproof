"""Command-line entry point. The converter and MCP server land in 0.1.0."""

import sys

from scoreproof import __version__

MESSAGE = f"""scoreproof {__version__}

The verified music-format converter (MusicXML, MIDI, ABC, MEI, Humdrum)
and its MCP server are in development and arrive in 0.1.0.
Follow along: https://github.com/asampat3090/scoreproof
"""


def main(argv=None):
    args = sys.argv[1:] if argv is None else argv
    if args in (["--version"], ["-V"]):
        print(__version__)
    else:
        print(MESSAGE, end="")
    return 0
