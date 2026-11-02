# scoreproof

**Convert any score. Prove nothing got lost.**

Scoreproof converts music files between MusicXML, MIDI, ABC, MEI and Humdrum, repairs the quirks of real notation-app exports (Sibelius, Dorico, Finale, MuseScore, Guitar Pro and others), and checks every conversion note by note, reporting exactly what was kept and what was lost.

Use it from any AI tool that speaks MCP (Claude, Cursor, VS Code, Gemini CLI, Codex and others), from the command line, or from Python.

## Use it from your AI tool

Scoreproof runs locally and works on files on your computer. It needs [uv](https://docs.astral.sh/uv/) (`brew install uv` or `pip install uv`).

**Claude Code**

```bash
claude mcp add scoreproof -- uvx scoreproof mcp
```

**Claude Desktop, Cursor, Gemini CLI**: add this to `claude_desktop_config.json`, `~/.cursor/mcp.json` or `~/.gemini/settings.json`:

```json
{
  "mcpServers": {
    "scoreproof": { "command": "uvx", "args": ["scoreproof", "mcp"] }
  }
}
```

**VS Code** (`.vscode/mcp.json`):

```json
{
  "servers": {
    "scoreproof": { "type": "stdio", "command": "uvx", "args": ["scoreproof", "mcp"] }
  }
}
```

**Codex** (`~/.codex/config.toml`):

```toml
[mcp_servers.scoreproof]
command = "uvx"
args = ["scoreproof", "mcp"]
```

Then ask things like:

- "Convert ~/Music/song.mid to MusicXML and tell me what got lost."
- "Transpose my lead sheet to Bb and show me the sheet music."
- "Split this score into parts for each player."
- "What changed between draft-1.musicxml and draft-2.musicxml?"
- "Check this Sibelius export before I send it to the copyist."

### Tools

| Tool | What it does |
|---|---|
| `convert` | Convert a file and verify the result note by note |
| `batch_convert` | Convert many files or whole folders |
| `inspect` | Summary (parts, ranges, key, meter, tempo) plus an ABC excerpt of chosen bars |
| `validate` | Find bars that don't add up, notes out of range, and repaired exporter quirks |
| `compare` | What changed between two versions, bar by bar, even across formats |
| `transpose` | By interval, into a key, or between concert and written pitch |
| `extract` | Pull out parts or bars, or make one file per player |
| `render` | Sheet music (SVG) or an audio preview (WAV) |
| `list_formats` | Formats and what each one cannot hold |

Results are written next to the input under a new name. Scoreproof never overwrites an existing file unless you give that exact output path.

## Use it from the command line

```bash
uvx scoreproof convert song.musicxml song.mid
uvx scoreproof inspect song.musicxml --bars 1-8
uvx scoreproof transpose song.musicxml --key "Bb major"
uvx scoreproof extract score.mxl --each-part
uvx scoreproof render song.musicxml --to wav
uvx scoreproof --help
```

Add `--json` to any command for machine-readable output.

## Use it from Python

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

Every conversion re-reads its own output and compares it with the source. When a format cannot hold something (MIDI has no chord symbols or lyrics layout; ABC and MEI exports carry transposing instruments at concert pitch), the report says so instead of failing silently.

Not supported: native Sibelius, Dorico, Finale and MuseScore files. Export MusicXML from the app first.

## Development

```bash
pip install -e ".[dev]"
pytest --cov=scoreproof        # coverage is kept at 100%
black src tests benchmarks
flake8 src tests benchmarks
```

The plan and milestones are in `docs/plan.md`. MIT licensed.
