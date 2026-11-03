# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

Scoreproof is a verified music-format converter: MusicXML, MIDI, ABC, MEI and Humdrum in and out, printable sheet music, exporter-quirk repairs on the way in, and a note-level fidelity report on every conversion. It ships as a Python library, a CLI, a local MCP server, a Claude Code plugin (`plugin/`) and a hosted service on Cloud Run (see `docs/plan.md`). It is free and open source, with no paid tier. It was split out of the `songchain` repo; history for these files is preserved.

## Commands

```bash
pip install -e ".[dev]"
pytest --cov=scoreproof                       # kept at 100%; CI gates at 95
black src tests benchmarks
flake8 src tests benchmarks                   # max-line-length 88, ignore E203
python benchmarks/score_corpus/fetch.py       # download the 430-file corpus
python benchmarks/score_corpus/run.py         # music21 vs scoreproof table
python benchmarks/played_midi/run.py          # played MIDI to notation table
```

## Architecture (`src/scoreproof/`)

Engine:
- `formats.py`: the `FORMATS` table, what each format cannot hold, extension sniffing (MEI inside `.xml`). `concert_pitch` marks formats whose writers drop transpositions.
- `musicxml.py`: XML-level repairs applied before music21 parses. Each rule in `_RULES` targets one exporter quirk measured on real files and records a `Fix`. Add a rule only with a test that shows the music21 failure on the raw XML and correct parsing after the repair.
- `io.py`: `load` and `Score.save`. Registers converter21 once. Played MIDI (notes off the grid) goes through `performance.interpret` instead of music21's reader. `_prepared_for` adjusts a stream before writing so writers cannot silently change the music (concert pitch, no chord symbols for MIDI, malformed repeats played once).
- `performance.py`: played MIDI to notation. Click or tracked beat (Ellis DP on note onsets), per-beat 16th/triplet snapping, release tidying, hand split, key spelling. Tune it against `benchmarks/played_midi/run.py`, not single files.
- `fidelity.py`: compares note events as a multiset of (onset, sounding pitch, duration) on a 1/48-quarter grid; `bar_differences` locates changes against the first file's bars.
- `convert.py`: `convert` (load, save, re-read, compare), `convert_any` (also pdf/png/svg/wav) and `batch_convert`.

Operations (shared by the CLI and MCP server, all return plain dicts):
- `analysis.py`: `inspect` (summary + ABC excerpt), `validate`, `compare`, part/bar selection.
- `transform.py`: `transpose`, `extract`.
- `render.py`: PDF/PNG (Verovio pages typeset by Typst), SVG via Verovio, WAV via pretty_midi.
- `paths.py`: output naming; default outputs never overwrite.

Front ends:
- `server.py`: MCP server (mcp SDK 2.x `MCPServer`). Tools wrap operations and turn `ScoreError` into `ToolError` so messages reach the client.
- `cli.py`: `scoreproof <command>`, `scoreproof mcp` runs the server.
- `hosted.py` + `drop.html`: `scoreproof serve`, the web service. The same operations as remote MCP tools that take a link or the score as text and return download links (`run_on` swaps scratch paths in results for links), plus the drop page, uploads, downloads and per-IP rate limits. Storage is a Cloud Storage bucket (`SCOREPROOF_BUCKET`) or a local folder. Deploy with `PROJECT=<id> deploy/cloudrun.sh`.

## Releasing

1. Bump the version in `pyproject.toml` and both fields of `server.json`, then `git tag vX.Y.Z && git push origin vX.Y.Z` (never `--tags`: an old songchain `v0.2.0` tag exists locally). `release.yml` checks the versions match, publishes to PyPI, then lists the server on the MCP registry.
2. Once PyPI has the release, bump `plugin/.claude-plugin/plugin.json` and the `scoreproof==` pins in `plugin/.mcp.json` and `plugin/pyproject.toml`, run `uv lock` in `plugin/`, and push to `main`; the Claude plugin directory picks `plugin/` up from there. Doing this before the release would point the live plugin at a version PyPI doesn't have. The directory accepts the lock only beside a plain `uvx scoreproof==<version>` command (a `uv run` command adds holds); it earns the Verified badge and is limited to CPython 3.12–3.13 on 64-bit macOS, Windows and Linux to stay under its 256 KiB file limit.

## Gotchas

- converter21 requires Python 3.12+.
- converter21 reads ABC without clefs; `io._with_clefs` adds the clef that suits each part.
- Hosted tests need the app's lifespan (`with TestClient(create_app())`), or `/mcp` fails with "Task group is not initialized".
- music21's MusicXML writer pads the final bar, so score length is measured as the end of the last note, not `highestTime`.
- Never use music21's own ABC reader; converter21 replaces it.
- Verovio must be pointed at its bundled fonts (`setResourcePath`): its default lookup fails on worker threads, which is where MCP tools run.
- music21's MIDI writer realises chord symbols as notes and ignores transpositions; `_prepared_for` handles both.
- mcp 2.x: `FastMCP` is now `MCPServer`; tests use `mcp.client.Client(server)` in-process.
- Typst cannot read WOFF2, so `render._text_font` unpacks Verovio's music font with fontTools (tempo marks set the note glyph as text). Typst's SVG reader also misplaces text containing `<title>`, which `_for_typst` strips.
- pretty_midi's `get_beats` follows the time signature (dotted quarters in 6/8); `performance` counts quarter notes from the tempo map instead.
