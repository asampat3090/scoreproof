# Score corpus benchmark

430 real MusicXML exports from 11 notation apps (Dorico, Finale, Flat, Guitar Pro, MuseScore 1–4, Noteflight, Notion, Sibelius native and Dolet), selected from 15 public sources. `manifest.csv` lists each file's source URL and licence. The files themselves are downloaded, never committed.

```bash
pip install -e ".[dev]"
python benchmarks/score_corpus/fetch.py   # downloads into files/
python benchmarks/score_corpus/run.py     # writes results/summary.md and results.csv
```

A file is *faithful* when the reader gets the same part count, bar count, note count and total length as a ground truth computed directly from the XML. The benchmark compares plain music21 with `scoreproof.load`, which repairs exporter quirks first.
