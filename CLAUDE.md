# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

Scoreproof is a verified music-format converter: MusicXML, MIDI, ABC, MEI and Humdrum in and out, exporter-quirk repairs on the way in, and a note-level fidelity report on every conversion. It ships as a Python library now, and as an MCP server, CLI and web drop zone next (see `docs/plan.md`). It was split out of the `songchain` repo; history for these files is preserved.

## Commands

```bash
pip install -e ".[dev]"
pytest --cov=scoreproof                       # kept at 100%; CI gates at 95
black src tests benchmarks
flake8 src tests benchmarks                   # max-line-length 88, ignore E203
python benchmarks/score_corpus/fetch.py       # download the 430-file corpus
python benchmarks/score_corpus/run.py         # music21 vs scoreproof table
```

## Architecture (`src/scoreproof/`)

Engine:
- `formats.py`: the `FORMATS` table, what each format cannot hold, extension sniffing (MEI inside `.xml`). `concert_pitch` marks formats whose writers drop transpositions.
- `musicxml.py`: XML-level repairs applied before music21 parses. Each rule in `_RULES` targets one exporter quirk measured on real files and records a `Fix`. Add a rule only with a test that shows the music21 failure on the raw XML and correct parsing after the repair.
- `io.py`: `load` and `Score.save`. Registers converter21 once. `_prepared_for` adjusts a stream before writing so writers cannot silently change the music (concert pitch, no chord symbols for MIDI).
- `fidelity.py`: compares note events as a multiset of (onset, sounding pitch, duration) on a 1/48-quarter grid; `bar_differences` locates changes against the first file's bars.
- `convert.py`: `convert` (load, save, re-read, compare) and `batch_convert`.

Operations (shared by the CLI and MCP server, all return plain dicts):
- `analysis.py`: `inspect` (summary + ABC excerpt), `validate`, `compare`, part/bar selection.
- `transform.py`: `transpose`, `extract`.
- `render.py`: SVG via Verovio, WAV via pretty_midi.
- `paths.py`: output naming; default outputs never overwrite.

Front ends:
- `server.py`: MCP server (mcp SDK 2.x `MCPServer`). Tools wrap operations and turn `ScoreError` into `ToolError` so messages reach the client.
- `cli.py`: `scoreproof <command>`, `scoreproof mcp` runs the server.

## Gotchas

- converter21 requires Python 3.12+.
- music21's MusicXML writer pads the final bar, so score length is measured as the end of the last note, not `highestTime`.
- Never use music21's own ABC reader; converter21 replaces it.
- Verovio must be pointed at its bundled fonts (`setResourcePath`): its default lookup fails on worker threads, which is where MCP tools run.
- music21's MIDI writer realises chord symbols as notes and ignores transpositions; `_prepared_for` handles both.
- mcp 2.x: `FastMCP` is now `MCPServer`; tests use `mcp.client.Client(server)` in-process.
