"""Score how faithfully real notation-app exports are read.

For every file in the corpus, a ground truth (parts, measures, notes, length)
is computed directly from the MusicXML. Two readers are compared with it:

- baseline: music21 on the file as exported
- songchain: ``songchain.score.load``, which repairs exporter quirks first

A file is faithful when parts, measures, note count and length all match.

    python benchmarks/score_corpus/fetch.py
    python benchmarks/score_corpus/run.py [--jobs 8]
"""

import argparse
import csv
import json
import signal
import warnings
import xml.etree.ElementTree as ET
from collections import defaultdict
from fractions import Fraction
from multiprocessing import Pool
from pathlib import Path

HERE = Path(__file__).parent
RESULTS = HERE / "results"
TIMEOUT_SECONDS = 120


def ground_truth(data: bytes) -> dict:
    from songchain.score import musicxml

    document = musicxml.read_document(data)
    if document[:2] in (b"\xff\xfe", b"\xfe\xff"):
        document = musicxml._XML_DECLARATION.sub("", document.decode("utf-16"))
    root = ET.fromstring(document)
    parts = root.findall("part")
    staves, notes, lengths = 0, 0, []
    for part in parts:
        total, part_staves = Fraction(0), 1
        for measure, divisions, bar_length in musicxml._part_measures(part):
            for value in measure.iter("staves"):
                part_staves = int(value.text)
            extent = musicxml._measure_extent(measure, divisions)
            if measure.find("note") is None and measure.get("implicit") != "yes":
                # An empty bar still lasts a full bar in every notation app.
                extent = max(extent, bar_length or 0)
            total += extent
            notes += sum(
                1
                for n in measure.findall("note")
                if n.find("grace") is None and n.find("rest") is None
            )
        staves += part_staves
        lengths.append(total)
    return {
        "parts": {len(root.findall("part-list/score-part")), staves},
        "measures": len(parts[0].findall("measure")) if parts else 0,
        "notes": notes,
        "length": float(max(lengths, default=0)),
    }


def measure_stream(stream) -> dict:
    parts = list(stream.parts) or [stream]
    notes = 0
    for element in stream.recurse().notes:
        if element.duration.isGrace or "ChordSymbol" in element.classes:
            continue
        notes += len(element.notes) if "ChordBase" in element.classes else 1
    return {
        "parts": len(parts),
        "measures": len(parts[0].getElementsByClass("Measure")),
        "notes": notes,
        "length": float(max(p.highestTime for p in parts)),
    }


def baseline(path: Path):
    import music21

    stream = music21.converter.parse(str(path), forceSource=True)
    if isinstance(stream, music21.stream.Opus):
        stream = stream.scores[0]
    return measure_stream(stream), []


def songchain_reader(path: Path):
    from songchain.score import load

    score = load(path)
    return measure_stream(score.stream), [f.rule for f in score.fixes]


READERS = {"baseline": baseline, "songchain": songchain_reader}


def _timeout(signum, frame):
    raise TimeoutError(f"over {TIMEOUT_SECONDS}s")


def score_file(row):
    path = HERE / "files" / row["id"]
    truth = ground_truth(path.read_bytes())
    results = []
    for name, reader in READERS.items():
        result = {"id": row["id"], "exporter": row["exporter"], "reader": name}
        signal.signal(signal.SIGALRM, _timeout)
        signal.alarm(TIMEOUT_SECONDS)
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                got, fixes = reader(path)
            checks = {
                "parts": got["parts"] in truth["parts"],
                "measures": got["measures"] == truth["measures"],
                "notes": got["notes"] == truth["notes"],
                "length": abs(got["length"] - truth["length"]) < 1e-3,
            }
            result.update(
                loaded=True,
                faithful=all(checks.values()),
                failed_checks=",".join(k for k, ok in checks.items() if not ok),
                fixes=",".join(fixes),
            )
        except Exception as exc:
            result.update(
                loaded=False,
                faithful=False,
                failed_checks="load",
                error=f"{type(exc).__name__}: {exc}"[:200],
            )
        finally:
            signal.alarm(0)
        results.append(result)
    return results


def summarize(rows):
    table = defaultdict(lambda: defaultdict(lambda: [0, 0, 0]))
    for r in rows:
        for key in (r["exporter"], "ALL"):
            cell = table[key][r["reader"]]
            cell[0] += 1
            cell[1] += r["loaded"]
            cell[2] += r["faithful"]
    lines = [
        "| Exporter | Files | music21 loads | music21 faithful "
        "| songchain loads | songchain faithful |",
        "|---|---|---|---|---|---|",
    ]
    order = sorted(k for k in table if k != "ALL") + ["ALL"]
    for key in order:
        b, s = table[key]["baseline"], table[key]["songchain"]
        lines.append(
            f"| {key} | {b[0]} | {b[1] / b[0]:.1%} | {b[2] / b[0]:.1%} "
            f"| {s[1] / s[0]:.1%} | {s[2] / s[0]:.1%} |"
        )
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--jobs", type=int, default=8)
    args = parser.parse_args()
    manifest = list(csv.DictReader(open(HERE / "manifest.csv", newline="")))
    with Pool(args.jobs, maxtasksperchild=20) as pool:
        rows = [r for batch in pool.imap_unordered(score_file, manifest) for r in batch]
    rows.sort(key=lambda r: (r["id"], r["reader"]))
    RESULTS.mkdir(exist_ok=True)
    fields = ["id", "exporter", "reader", "loaded", "faithful", "failed_checks"]
    fields += ["fixes", "error"]
    with open(RESULTS / "results.csv", "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)
    table = summarize(rows)
    (RESULTS / "summary.md").write_text(table + "\n")
    print(table)
    fixed = [r for r in rows if r["reader"] == "songchain" and r.get("fixes")]
    print(json.dumps({"files_with_fixes": len(fixed)}))


if __name__ == "__main__":
    main()
