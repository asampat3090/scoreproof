"""Download the benchmark corpus listed in manifest.csv into files/.

The files are real exports from notation apps, fetched from their public
sources. Many have no redistribution licence, so they are never committed.

    python benchmarks/score_corpus/fetch.py
"""

import csv
import shutil
import sys
import urllib.request
from pathlib import Path

HERE = Path(__file__).parent
FILES = HERE / "files"


def local_path(row) -> Path:
    return FILES / row["id"]


def fetch(row) -> str:
    target = local_path(row)
    if target.exists():
        return "cached"
    target.parent.mkdir(parents=True, exist_ok=True)
    url = row["url"]
    if url.startswith("music21-corpus:"):
        import music21

        corpus_dir = Path(music21.__file__).parent / "corpus"
        shutil.copyfile(corpus_dir / url.split(":", 1)[1], target)
        return "copied"
    with urllib.request.urlopen(url, timeout=60) as response:
        target.write_bytes(response.read())
    return "downloaded"


def main():
    rows = list(csv.DictReader(open(HERE / "manifest.csv", newline="")))
    failed = 0
    for i, row in enumerate(rows, 1):
        try:
            status = fetch(row)
        except Exception as exc:
            status, failed = f"FAILED {exc}", failed + 1
        print(f"[{i}/{len(rows)}] {status:10} {row['id']}")
    print(f"{len(rows) - failed} of {len(rows)} files available in {FILES}")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
