# Plan: an accurate music-format converter as an MCP server

Goal: one tool every AI client can call on day one to convert between music formats, with measurable accuracy. Converting files in music production is tedious, and the validation tests show the open-source parsers are only partly reliable. Accuracy is the product.

Evidence behind this plan: `reports/SongChain score toolkit validation tests.md` and `reports/SongChain agentic landscape positioning.md`.

## Status (2026-10-28)

**M0 done.**
- **Licences:** music21 (BSD-3), converter21 (MIT), mcp SDK (MIT) and pretty_midi (MIT) fit an MIT package. verovio and PyGuitarPro are LGPL-3.0, which is fine as ordinary pip dependencies but means their code is never vendored. Of the 430 corpus files, 211 are redistributable (CC-BY-4.0, MIT, BSD, Apache-2.0); the rest are fetched for benchmarking and never committed.
- **Packaging:** `import songchain` no longer pulls in torch, so `songchain.score` installs light (`pip install songchain[score]`).

**M1 core engine landed** in `songchain/score/`:
- **Formats:** reads and writes MusicXML/.mxl, MIDI, ABC, MEI and Humdrum.
- **Quirk repairs:** 10 rules, each reproduced on a synthetic file and checked against music21.
- **Fidelity:** every conversion re-reads its output and compares it with the source note by note.

Benchmark on 430 real exports from 11 apps (`benchmarks/score_corpus/`):

| | music21 alone | songchain |
|---|---|---|
| Loads | 96.0% | **100%** |
| Faithful (parts, bars, notes, length) | 87.9% | **95.6%** |

Remaining gaps:
- **Benchmark misses:** 19 files, all length or part-count mismatches, several of them ground-truth artefacts (a file with no parts, an absurd encoded duration).
- **Known conversion loss:** ties on chords crossing a barline are lost in the ABC round trip (converter21).
- **Next:** the stdio MCP server and CLI (M2), and DAW MIDI to notation (M3).

## What it does

Goal: cover everything a musician or agent needs to do with a score or MIDI file, without the model ever editing raw XML. Tools are grouped so that each one has a clear job; too many overlapping tools makes clients pick the wrong one, so related operations share a tool with a `mode` or option.

**Core (convert and check)**

| Tool | What it does |
|---|---|
| `convert(input, to, options)` | Converts between formats and returns the output plus a fidelity report. Options: MIDI quantisation grid, key and meter hints, concert or transposed pitch, keep or expand repeats |
| `batch_convert(inputs or folder, to)` | Converts many files in one call and returns one report row per file |
| `inspect(input, bars?, parts?)` | Compact summary (title, composer, parts and instruments, key, meter, tempo map, bar count, range per part) plus an ABC window of the requested bars. Never returns raw MusicXML |
| `validate(input)` | Deterministic checks: bar fill, dangling ties, out-of-range notes per instrument, schema validity, exporter quirks found and fixed |
| `compare(a, b)` | Note-level diff of two files or versions: what changed, by bar and part. Reuses the fidelity engine |
| `list_formats()` | Supported format pairs and what each pair loses |

**Transform (the everyday chores)**

| Tool | What it does |
|---|---|
| `transpose(input, interval or key, to_concert?)` | Transposes a score, or moves between concert and written pitch for transposing instruments |
| `extract(input, parts?, bars?)` | Pulls out individual parts, or an excerpt of bars, as a new file |
| `merge(inputs)` | Combines parts or movements into one score |
| `edit(input, bars, abc)` | Replaces bars with ABC the model wrote, then validates the result. The only write path for the model, so edits stay small and checkable |
| `set_metadata(input, fields)` | Sets title, composer, instrument names, tempo |

**Produce (outputs people actually use)**

| Tool | What it does |
|---|---|
| `render(input, to)` | Sheet music to SVG, PDF or PNG (verovio); audio to WAV or MP3 (FluidSynth when available, else the built-in synth) |
| `lead_sheet(input)` | Melody, chord symbols and lyrics only |
| `tab(input)` | Guitar or bass tab (text, or Guitar Pro) |
| `transcribe(audio)` | Audio to MIDI via Basic Pitch (optional extra), then on to notation through `convert` |

**Analyse (read-only answers)**

| Tool | What it does |
|---|---|
| `analyze(input, what)` | Key estimation, chord labels per bar, tempo and meter changes, instrument ranges, MIDI track, channel and program map (General MIDI names, drum map) |

**MCP features beyond tools**
- **Resources:** the format-loss matrix, per-format notes, and the quirk rules, so clients can read them without a tool call.
- **Prompts:** ready-made workflows such as "clean up DAW MIDI into a readable score", "make parts for players", "check this export before sending".
- **Structured output and progress:** typed JSON results for every tool, and progress notifications for batch and render jobs.

v1 ships the core group plus `transpose`, `extract` and `render`. The rest follow in M3–M4, in order of demand.

Every `convert` returns a **fidelity report**: note, measure and part counts before and after, the features the target format cannot hold (from a static loss matrix), the features that were actually dropped (measured by re-parsing the output), and a confidence level. This is the differentiator: other converters fail silently.

## Format matrix (v1)

| Format | Read | Write | Backend |
|---|---|---|---|
| MusicXML / .mxl | yes | yes | music21 + our exporter-quirk normaliser |
| MIDI (.mid) | yes | yes | music21 / pretty_midi; quantisation for MIDI → notation |
| ABC | yes | yes | converter21 reader, xml2abc writer (never music21's ABC reader) |
| MEI | yes | yes | converter21, verovio cross-check |
| Humdrum **kern | yes | yes | converter21 |
| Guitar Pro 3–5 | yes | yes | PyGuitarPro (newer .gp/.gpx: no) |
| MuseScore .mscz | optional | optional | MuseScore CLI, only when installed |
| SVG / PDF | – | yes | verovio |
| WAV | – | yes | existing `MIDIClip.synthesize`; FluidSynth optional |
| Audio → MIDI | yes | – | Basic Pitch (existing adapter), optional extra |

Not supported, stated plainly in the docs: native `.sib`, `.dorico` and `.musx`. Their own apps export MusicXML, and we handle that MusicXML better than anyone.

## Accuracy: how "very good" is measured

1. **Golden corpus.** Reuse the 430-file, 11-exporter corpus from the parsing test as regression fixtures. Only redistributable files go in the repo; the rest are fetched in CI.
2. **Normaliser.** The ten measured exporter-quirk rules (UTF-16 Dolet files, note-free Dorico bars, directions inside Guitar Pro chords, dynamics inside notations, dangling Sibelius ties, octave-only transposes, rare accidentals, irregular bars, part names, absurd durations).
3. **Target metric.** Raise "faithful on parts, measures, notes and duration" from the measured 87% (best single library) to **≥ 97%** across the corpus. Hold it in CI.
4. **Round-trip tests.** For every format pair: score → X → score, compared with the original at note level (pitch, onset, duration, voice, lyrics, chord symbols).
5. **MIDI → notation is the hard case.** DAW MIDI has no bars, spelling or voices, so quantisation, key-aware pitch spelling, voice and hand separation, and tuplet detection need dedicated work. Measure it by score → MIDI → score against the original. This is where producers feel the pain most, so it gets the most engineering time.
6. **Public benchmark.** Publish the corpus results against bare music21, verovio and the MuseScore CLI. This is the launch proof point.

## Reach on day one

| Surface | Who it reaches | How |
|---|---|---|
| Local MCP server (stdio) | Claude Desktop, Claude Code, Cursor, VS Code, Windsurf, Gemini CLI, Codex | `uvx songchain-mcp`, file paths in and out, no setup |
| Remote MCP server (Streamable HTTP) | claude.ai and ChatGPT connectors, any client without a filesystem | Input by URL or inline text; output as a short-lived download link plus the compact summary |
| CLI | Producers and agents with a shell | `songchain convert in.musicxml out.mid` |
| Web drop zone | People with no AI tool; needed for Product Hunt | Drag a file in, pick a format, download. Same core |
| Claude Code plugin | Claude Code users | Plugin bundling the MCP config and a short skill on when to use which tool |

Listings at launch: the official MCP registry, Smithery, Glama, PulseMCP, mcp.so, awesome-mcp-servers, and our own Claude Code plugin marketplace (then submit to Anthropic's).

Design rules from the research:
- Never put full MusicXML into the model's context; return summaries and ABC windows (about 30× fewer tokens).
- Do not pass binaries as base64 through the model; use paths locally and links remotely.
- Keep it deterministic and say so: "converts and checks your files, never writes your music."

## Milestones

| # | Scope | Estimate |
|---|---|---|
| M0 | Rerun token counts on Claude's tokenizer; check the licences of dependencies (verovio, xml2abc, PyGuitarPro, converter21) and of corpus files; choose a name | 1 day |
| M1 | Core engine: score model, readers/writers for MusicXML, MIDI, ABC, MEI and kern, normaliser, fidelity report, golden tests | 2 weeks |
| M2 | Stdio MCP server + CLI with the core tools, `transpose`, `extract`, `render`, resources and prompts; `uvx` install; registry listings | 1–2 weeks |
| M3 | MIDI → notation quality work; Guitar Pro; optional MuseScore backend | 1–2 weeks |
| M4 | Remote MCP server + web drop zone, with rate limits and auto-deleting storage | 1 week |
| M5 | Launch: benchmark post, 60-second demo (DAW MIDI → clean score in chat), Show HN, MCP directories, then Product Hunt on the web drop zone | 1 week |

Estimates are rough for one developer.

## What happens to the current code

- Keep: `MIDIClip` / `AudioClip`, `MIDIClip.synthesize`, the Basic Pitch adapter, the `require()` optional-dependency pattern and the 100%-coverage test discipline.
- Demote: `Chain`, `compose.py` and the audiocraft adapters stay in the package, but leave the README headline. Remove them later if nothing uses them.
- New: `songchain/score/` (model, readers, writers, normaliser, fidelity), `songchain/mcp/` (server), `songchain/cli.py`.

## Open decisions

1. **Name: decided — Scoreproof.** "-proof" as in waterproof (the music survives the trip) and an engraver's proof (the check copy before print), which points at the fidelity report. Free on PyPI and npm; scoreproof.io, .app and .dev unregistered; no USPTO results (checked 2026-10-30). Tagline: "Convert any score. Prove nothing got lost." Rejected: Petrucci (IMSLP's registered PETRUCCI MUSIC LIBRARY mark), Legato (registered Class 15 mark), Ossia (ossia score), Attacca (PyPI taken), Solfa (heard as "sofa"), and the single-word musical terms, which are all taken.
2. **Remote hosting.** Who pays for, and runs, the hosted endpoint and drop zone (small, but not zero, with file storage and abuse limits).
3. **Licence fit.** Some converters are LGPL or GPL; check them before committing to an MIT distribution.

## Risks

- MIDI → notation accuracy may plateau below what producers expect. Publish the measured numbers rather than promise perfection.
- The MuseScore CLI already converts formats for people who have MuseScore installed. Our edge is no install, AI-client access, the quirk normaliser and the fidelity report.
- Demand from composers is weak and hostile to generative AI. Converting is a chore, not a creative act, which is why this framing fits.
