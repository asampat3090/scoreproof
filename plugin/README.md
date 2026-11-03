# Scoreproof

Convert music files and get sheet music you can print and play. Scoreproof reads and writes MusicXML, MIDI, ABC, MEI and Humdrum, makes PDF and PNG sheet music, and checks every conversion note by note so you know exactly what was kept and what was lost.

MIDI you played into a DAW or from a keyboard comes out as readable notation: Scoreproof finds the beat (even without a click), snaps rhythms to 16ths or triplets, tidies note lengths, splits piano into two hands and spells notes in the right key.

## Install

In Claude Code:

```
/plugin install scoreproof --marketplace asampat3090/scoreproof
```

It needs [uv](https://docs.astral.sh/uv/) (`brew install uv` or `pip install uv`).

## Try it

- "Turn ~/Music/take-3.mid into sheet music I can print."
- "Convert song.mid to MusicXML so I can open it in Sibelius."
- "Transpose my lead sheet to Bb and make a PDF."
- "Split this score into parts for each player."
- "What changed between draft-1.musicxml and draft-2.musicxml?"

## Tools

`convert`, `batch_convert`, `inspect`, `validate`, `compare`, `transpose`, `extract`, `render` (PDF, PNG, SVG, WAV) and `list_formats`.

## What it runs

The plugin starts a local MCP server with `uvx scoreproof==<version> mcp`, which downloads the open-source [scoreproof](https://pypi.org/project/scoreproof/) package from PyPI the first time.

Everything happens on your computer. Scoreproof reads only the files you point it at and writes results next to them under new names; it never overwrites an existing file unless you give that exact path. It sends no data anywhere and makes no network requests.

Source: https://github.com/asampat3090/scoreproof (MIT licensed).
