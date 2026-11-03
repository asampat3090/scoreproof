"""The Scoreproof MCP server: music-file tools for any AI client.

Run it with ``scoreproof mcp`` (stdio). Every tool works on files on this
computer and writes results next to the input, never over an existing file.
"""

import functools
import json
from typing import Annotated, Any, Literal

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp_types import ToolAnnotations
from pydantic import Field

from scoreproof import __version__, analysis, formats, musicxml, transform
from scoreproof.convert import batch_convert as batch_convert_files
from scoreproof.convert import convert_any
from scoreproof.errors import ScoreError
from scoreproof.render import render as render_score

INSTRUCTIONS = """\
Scoreproof converts, checks and transforms music files on this computer:
MusicXML (.musicxml, .xml, .mxl), MIDI (.mid), ABC (.abc), MEI (.mei) and
Humdrum (.krn). It also makes printable sheet music (PDF, PNG) and audio.

- Pass absolute file paths. Results are written next to the input under a new
  name unless an output path is given; existing files are never overwritten.
- Every conversion is verified note by note. Tell the user the confidence and
  anything that was lost.
- To read or discuss the music, use `inspect`: it returns a summary and an ABC
  excerpt. Do not open MusicXML files directly; they are very large.
- When the user wants sheet music to read or print, convert to pdf.
- MIDI played in a DAW or on a keyboard is read as notation automatically:
  the beat is found, rhythms are snapped and piano parts split into hands.
  Results include an `interpretation`; mention the tempo and key it chose.
- Native Sibelius, Dorico, Finale and MuseScore files are not supported. Ask
  the user to export MusicXML from their app.
"""

READ_ONLY = ToolAnnotations(readOnlyHint=True, openWorldHint=False)
WRITES_NEW_FILE = ToolAnnotations(
    readOnlyHint=False,
    destructiveHint=False,
    idempotentHint=False,
    openWorldHint=False,
)

server = MCPServer(
    "scoreproof",
    title="Scoreproof",
    instructions=INSTRUCTIONS,
    version=__version__,
    website_url="https://github.com/asampat3090/scoreproof",
)

Path_ = Annotated[str, Field(description="Absolute path to a music file")]
Output = Annotated[
    str | None,
    Field(description="Output file or folder (default: next to the input)"),
]
Target = Annotated[
    str,
    Field(
        description="Target format: musicxml, mxl, midi, abc, mei, humdrum, "
        "or pdf/png/svg (sheet music) or wav (audio)"
    ),
]
Parts = Annotated[
    list[str] | None,
    Field(description="Parts by number (1-based) or name; default all"),
]
Bars = Annotated[str | None, Field(description='Bar range such as "1-8", "5" or "9-"')]


def _scoreproof_errors(fn):
    """Show the user what went wrong instead of a generic tool failure."""

    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except ScoreError as exc:
            raise ToolError(str(exc)) from exc

    return wrapper


@server.tool(name="convert", title="Convert a music file", annotations=WRITES_NEW_FILE)
@_scoreproof_errors
def convert_file(input: Path_, to: Target, output: Output = None) -> dict[str, Any]:
    """Convert a score between formats and verify the result note by note,
    or turn it into sheet music (pdf, png, svg) or audio (wav).

    Returns the output path, a confidence level (exact, high, medium, low),
    the share of notes kept, features the target format cannot hold, and
    exporter quirks that were repaired on the way in.
    """
    return convert_any(input, output, to)


@server.tool(title="Convert many music files", annotations=WRITES_NEW_FILE)
@_scoreproof_errors
def batch_convert(
    inputs: Annotated[list[str], Field(description="Files and/or folders")],
    to: Target,
    output_dir: Annotated[
        str | None,
        Field(description="Folder for the results (default: beside each input)"),
    ] = None,
) -> dict[str, Any]:
    """Convert files, or every score inside folders, to one format.

    One failure does not stop the batch; each file gets its own row.
    """
    return batch_convert_files(inputs, to, output_dir)


@server.tool(title="Look at a score", annotations=READ_ONLY)
@_scoreproof_errors
def inspect(
    input: Path_,
    bars: Annotated[
        str | None,
        Field(description='Bars to show as ABC, e.g. "1-8" (max 32); "" for none'),
    ] = analysis.DEFAULT_EXCERPT,
    parts: Parts = None,
) -> dict[str, Any]:
    """Summarise a score and show some bars as ABC notation.

    Gives title, composer, parts with instruments and ranges, key signature
    and estimated key, meters, tempos and bar count, plus an ABC excerpt of
    the requested bars to read the actual notes.
    """
    return analysis.inspect(input, bars or None, parts)


@server.tool(title="Check a score for problems", annotations=READ_ONLY)
@_scoreproof_errors
def validate(input: Path_) -> dict[str, Any]:
    """Check a score: exporter quirks that needed repair, bars that do not
    fill their time signature, and notes outside an instrument's range."""
    return analysis.validate(input)


@server.tool(title="Compare two versions", annotations=READ_ONLY)
@_scoreproof_errors
def compare(
    a: Annotated[str, Field(description="Absolute path to the first version")],
    b: Annotated[str, Field(description="Absolute path to the second version")],
) -> dict[str, Any]:
    """Show what changed between two versions of a piece, bar by bar.

    The files may be in different formats (e.g. an original MusicXML and a
    MIDI export of it).
    """
    return analysis.compare(a, b)


@server.tool(title="Transpose a score", annotations=WRITES_NEW_FILE)
@_scoreproof_errors
def transpose(
    input: Path_,
    interval: Annotated[
        str | None,
        Field(description='Interval ("M2", "-m3", "P5") or semitones ("2", "-3")'),
    ] = None,
    key: Annotated[
        str | None, Field(description='Target key, e.g. "D", "Bb major", "F# minor"')
    ] = None,
    pitch: Annotated[
        Literal["concert", "written"] | None,
        Field(description="Switch transposing instruments to concert or written pitch"),
    ] = None,
    output: Output = None,
    to: Annotated[
        str | None, Field(description="Output format (default: same)")
    ] = None,
) -> dict[str, Any]:
    """Transpose a score by an interval, into a key, or between concert and
    written pitch. Give exactly one of interval, key or pitch."""
    return transform.transpose(input, interval, key, pitch, output, to)


@server.tool(title="Extract parts or bars", annotations=WRITES_NEW_FILE)
@_scoreproof_errors
def extract(
    input: Path_,
    parts: Parts = None,
    bars: Bars = None,
    each_part: Annotated[
        bool, Field(description="One file per part (to make parts for players)")
    ] = False,
    output: Output = None,
    to: Annotated[
        str | None, Field(description="Output format (default: same)")
    ] = None,
) -> dict[str, Any]:
    """Save chosen parts and/or bars of a score as new files."""
    return transform.extract(input, parts, bars, each_part, output, to)


@server.tool(title="Render sheet music or audio", annotations=WRITES_NEW_FILE)
@_scoreproof_errors
def render(
    input: Path_,
    to: Annotated[
        Literal["pdf", "png", "svg", "wav"],
        Field(
            description="pdf: printable sheet music; png: page images; "
            "svg: pages for screens; wav: audio preview"
        ),
    ] = "pdf",
    page_size: Annotated[
        Literal["letter", "a4"], Field(description="Paper size for pdf and png")
    ] = "letter",
    output: Output = None,
) -> dict[str, Any]:
    """Engrave sheet music to print or view (PDF, PNG, SVG) or make an audio
    preview (WAV) so the user can see, play or hear the score."""
    return render_score(input, to, output, page_size)


@server.tool(title="List supported formats", annotations=READ_ONLY)
def list_formats() -> dict[str, Any]:
    """Formats Scoreproof reads and writes, and what each one cannot hold."""
    return formats.describe_formats()


@server.resource(
    "scoreproof://formats",
    name="formats",
    description="Supported formats and what converting into each one loses",
    mime_type="application/json",
)
def formats_resource() -> str:
    return json.dumps(formats.describe_formats(), indent=2)


@server.resource(
    "scoreproof://repairs",
    name="repairs",
    description="Exporter quirks repaired automatically when reading MusicXML",
    mime_type="application/json",
)
def repairs_resource() -> str:
    return json.dumps(musicxml.describe_repairs(), indent=2)


@server.prompt(title="Turn MIDI into a readable score")
def midi_to_score(path: str) -> str:
    """Convert a MIDI file from a DAW into notation and check it."""
    return (
        f"Turn the MIDI file at {path} into a readable score. Use inspect to see "
        "its parts, key, meter and how it was interpreted. Convert it to "
        "MusicXML (to edit) and to PDF (to read), and tell me the tempo and key "
        "you chose and anything that looks wrong, such as odd rhythms."
    )


@server.prompt(title="Make parts for players")
def make_parts(path: str) -> str:
    """Split a score into one file per instrument."""
    return (
        f"Make individual parts from the score at {path}. Use inspect to list "
        "the parts, then extract with each_part set to true. If any part is "
        "for a transposing instrument, check it is at written pitch. Render "
        "each part to PDF and list the files."
    )


@server.prompt(title="Check an export before sending")
def check_export(path: str) -> str:
    """Review a score file for problems before sharing it."""
    return (
        f"Check the score at {path} before I send it to anyone. Run validate "
        "and inspect, then tell me in plain words what was repaired, any bars "
        "that do not add up, and any notes out of range, with bar numbers."
    )


def main():
    server.run("stdio")
