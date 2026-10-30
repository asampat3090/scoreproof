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

- `formats.py`: the `FORMATS` table, what each format cannot hold, and extension sniffing (MEI inside `.xml`).
- `musicxml.py`: XML-level repairs applied before music21 parses. Each rule in `_RULES` targets one exporter quirk measured on real files, and records a `Fix`. Add a rule only with a test that shows the music21 failure on the raw XML and correct parsing after the repair.
- `io.py`: `load` and `Score.save`. Registers converter21 once for ABC, MEI and Humdrum.
- `fidelity.py`: compares note events as a multiset of (onset, sounding pitch, duration) on a 1/48-quarter grid. MIDI is a performance format, so its comparison expands repeats first.
- `convert.py`: load, save, re-read, compare; returns a `ConversionResult`.

## Gotchas

- converter21 requires Python 3.12+.
- music21's MusicXML writer pads the final bar, so score length is measured as the end of the last note, not `highestTime`.
- Never use music21's own ABC reader; converter21 replaces it.
