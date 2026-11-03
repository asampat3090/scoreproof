"""The ``scoreproof`` command: convert, check and transform music files."""

import argparse
import json
import sys
import textwrap

from scoreproof import __version__, analysis, formats, transform
from scoreproof.convert import batch_convert, convert_any
from scoreproof.errors import ScoreError
from scoreproof.render import PAGE_SIZES, RENDER_FORMATS, render

EXAMPLES = """\
examples:
  scoreproof convert song.musicxml song.mid
  scoreproof convert song.mid --to musicxml
  scoreproof convert recording.mid --to pdf     # sheet music to print
  scoreproof inspect song.musicxml --bars 1-8
  scoreproof transpose song.musicxml --key "Bb major"
  scoreproof extract score.mxl --each-part
  scoreproof render song.musicxml --to png --page-size a4
  scoreproof mcp          # run the MCP server for AI tools
  scoreproof serve        # run the web service (drop page and MCP over HTTP)
"""


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="scoreproof",
        description="Convert any score. Prove nothing got lost.",
        epilog=EXAMPLES,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("-V", "--version", action="version", version=__version__)
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument(
        "--json", action="store_true", help="print the full result as JSON"
    )
    commands = parser.add_subparsers(dest="command", metavar="command")

    def command(name, help_text):
        return commands.add_parser(name, help=help_text, parents=[common])

    p = command("convert", "convert a file (or make sheet music) and verify it")
    p.add_argument("input")
    p.add_argument("output", nargs="?", help="output file or folder")
    p.add_argument(
        "--to", help="musicxml, mxl, midi, abc, mei, humdrum, pdf, png, svg or wav"
    )

    p = command("batch", "convert many files or folders")
    p.add_argument("inputs", nargs="+")
    p.add_argument("--to", required=True)
    p.add_argument("-o", "--output-dir")

    p = command("inspect", "summarise a score and show bars as ABC")
    p.add_argument("input")
    p.add_argument(
        "--bars", default=analysis.DEFAULT_EXCERPT, help='e.g. 1-8; "" for none'
    )
    p.add_argument("--parts", help="comma-separated part numbers or names")

    p = command("validate", "check a score for problems")
    p.add_argument("input")

    p = command("compare", "show what changed between two versions")
    p.add_argument("a")
    p.add_argument("b")

    p = command("transpose", "transpose by an interval, into a key, or concert/written")
    p.add_argument("input")
    how = p.add_mutually_exclusive_group(required=True)
    how.add_argument("--interval", help="M2, -m3, P5, or semitones such as 2 or -3")
    how.add_argument("--key", help='"D", "Bb major", "F# minor"')
    how.add_argument("--concert", action="store_const", const="concert", dest="pitch")
    how.add_argument("--written", action="store_const", const="written", dest="pitch")
    p.add_argument("-o", "--output")
    p.add_argument("--to", help="output format (default: same as input)")

    p = command("extract", "save chosen parts and/or bars as new files")
    p.add_argument("input")
    p.add_argument("--parts", help="comma-separated part numbers or names")
    p.add_argument("--bars", help="e.g. 1-8")
    p.add_argument("--each-part", action="store_true", help="one file per part")
    p.add_argument("-o", "--output")
    p.add_argument("--to", help="output format (default: same as input)")

    p = command("render", "engrave sheet music (PDF, PNG, SVG) or audio (WAV)")
    p.add_argument("input")
    p.add_argument("--to", choices=list(RENDER_FORMATS), default="pdf")
    p.add_argument("--page-size", choices=list(PAGE_SIZES), default="letter")
    p.add_argument("-o", "--output")

    command("formats", "list supported formats and what each loses")
    commands.add_parser("mcp", help="run the MCP server over stdio for AI tools")
    p = commands.add_parser("serve", help="run the web service: drop page and MCP")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8080)
    return parser


def run(args) -> dict:
    """Do what the parsed command asks and return its result."""
    if args.command == "convert":
        return convert_any(args.input, args.output, args.to)
    if args.command == "batch":
        return batch_convert(args.inputs, args.to, args.output_dir)
    if args.command == "inspect":
        return analysis.inspect(args.input, args.bars or None, args.parts)
    if args.command == "validate":
        return analysis.validate(args.input)
    if args.command == "compare":
        return analysis.compare(args.a, args.b)
    if args.command == "transpose":
        return transform.transpose(
            args.input, args.interval, args.key, args.pitch, args.output, args.to
        )
    if args.command == "extract":
        return transform.extract(
            args.input, args.parts, args.bars, args.each_part, args.output, args.to
        )
    if args.command == "render":
        return render(args.input, args.to, args.output, args.page_size)
    return formats.describe_formats()


def main(argv=None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command is None:
        parser.print_help()
        return 0
    if args.command == "mcp":
        from scoreproof.server import main as serve

        serve()
        return 0
    if args.command == "serve":
        from scoreproof.hosted import main as serve_web

        serve_web(args.host, args.port)
        return 0
    try:
        result = run(args)
    except ScoreError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps(result, indent=2, default=str))
    else:
        show(result)
    return 0


def show(value, depth=0):
    """Print a result as readable indented text, skipping empty fields."""
    pad = "  " * depth
    if isinstance(value, dict):
        for key, item in value.items():
            if item in (None, "", [], {}):
                continue
            label = key.replace("_", " ")
            if isinstance(item, (dict, list)):
                print(f"{pad}{label}:")
                show(item, depth + 1)
            elif isinstance(item, str) and "\n" in item:
                print(f"{pad}{label}:")
                print(textwrap.indent(item.rstrip(), pad + "  "))
            else:
                print(f"{pad}{label}: {item}")
    elif isinstance(value, list):
        for item in value:
            if isinstance(item, dict):
                scalars = {
                    k: v for k, v in item.items() if not isinstance(v, (dict, list))
                }
                nested = {k: v for k, v in item.items() if isinstance(v, (dict, list))}
                line = ", ".join(
                    f"{k.replace('_', ' ')}: {v}" for k, v in scalars.items()
                )
                print(f"{pad}- {line}")
                show(nested, depth + 1)
            else:
                print(f"{pad}- {item}")
