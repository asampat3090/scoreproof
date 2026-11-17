# Contributing to Scoreproof

Thank you for being here. Scoreproof is a free, open source passion project, and it gets better through the music and code people bring to it. You don't need to be an expert in music notation or in Python to help: musicians testing real files are as valuable as developers.

**Where to start:** issues labelled [good first issue](https://github.com/asampat3090/scoreproof/labels/good%20first%20issue) are small and well scoped; [help wanted](https://github.com/asampat3090/scoreproof/labels/help%20wanted) ones are bigger. Comment on an issue to claim it, and ask anything in [Discussions](https://github.com/asampat3090/scoreproof/discussions).

The most useful contributions:

- **Files that convert badly.** A real export (MusicXML from Sibelius, Dorico, Finale, MuseScore, Guitar Pro and others) or a MIDI take that comes out wrong is the best bug report there is. Open an issue with the file, the app and version that made it, and what looks wrong. Only share files you have the right to share.
- **Exporter quirk repairs.** Each rule in `src/scoreproof/musicxml.py` fixes one quirk measured on real files. Add a rule together with a test that shows music21 failing on the raw XML and reading it correctly after the repair.
- **Played-MIDI accuracy.** Tune `src/scoreproof/performance.py` against `benchmarks/played_midi/run.py`, not single files, and include the before and after numbers in the pull request.
- **New formats and tools**, discussed in an issue first.

## Setup

```bash
pip install -e ".[dev]"
pytest --cov=scoreproof        # coverage is kept at 100%
black src tests benchmarks
flake8 src tests benchmarks
scoreproof serve               # the web service at http://127.0.0.1:8080
```

Keep pull requests focused, use conventional commit messages (`feat:`, `fix:`, `docs:`), and add tests for any change in behaviour. `CLAUDE.md` describes the architecture and the gotchas.
