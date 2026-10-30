# scoreproof

**Convert any score. Prove nothing got lost.**

Scoreproof converts music files between MusicXML, MIDI, ABC, MEI and Humdrum, repairs the quirks of real notation-app exports (Sibelius, Dorico, Finale, MuseScore, Guitar Pro and others), and checks every conversion note by note, reporting exactly what was kept and what was lost.

It will be available as an MCP server for AI tools (Claude, ChatGPT, Gemini, Cursor and others), as a command-line tool and on the web. The Python library works today; the MCP server and CLI arrive in 0.1.0.

## Use it from Python

```bash
pip install -e .
```

```python
from scoreproof import convert

result = convert("song.musicxml", "song.mid")
result.confidence        # "exact", "high", "medium" or "low"
result.expected_losses   # what MIDI cannot hold: lyrics, chord symbols, ...
result.fixes             # exporter quirks repaired on the way in
```

## How accurate is it?

On 430 real exports from 11 notation apps (`benchmarks/score_corpus/`):

| | music21 alone | scoreproof |
|---|---|---|
| Files that load | 96.0% | **100%** |
| Read faithfully (parts, bars, notes, length) | 87.9% | **95.6%** |

## Development

```bash
pip install -e ".[dev]"
pytest --cov=scoreproof        # coverage is kept at 100%
black src tests benchmarks
flake8 src tests benchmarks
```

The plan and milestones are in `docs/plan.md`. The npm package in `npm/` is a placeholder that reserves the name.

MIT licensed.
