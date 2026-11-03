"""How readable is MIDI that someone played, once read as notation?

Each piece from the music21 corpus is exported to exact MIDI, then
"performed" the way a person at a keyboard would (see tests/played.py):
one piano track, starts up to ~30 ms early or late, notes released early
or held into the next, and in the no-click runs a tempo the file does not
state, drifting by 3%. Both readings are compared with the exact notes:

- placed: notes found at the right beat with the right pitch
- lengths: of those, notes with the right written length too

The no-click readings are compared at the best whole-beat shift and at
the metrical level the tracker chose (half or double time counts as a
miss for that piece, and is reported).

    python benchmarks/played_midi/run.py
"""

import sys
import tempfile
import warnings
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tests"))
warnings.filterwarnings("ignore")

from played import perform  # noqa: E402

from scoreproof import convert, load  # noqa: E402
from scoreproof.fidelity import note_events  # noqa: E402

PIECES = {
    "Bach chorale BWV 66.6": ("bach/bwv66.6", None),
    "Foster, Jeanie (melody)": ("leadSheet/fosterBrownHair.mxl", None),
    "Joplin, Maple Leaf Rag": ("joplin/maple_leaf_rag.mxl", None),
    "Schumann, Op. 41/1 ii (6/8)": ("schumann_robert/opus41no1/movement2", 24),
    "Beethoven, Op. 18/1 i (3/4)": ("beethoven/opus18no1/movement1", 24),
}
PLAYINGS = {
    "click": dict(seed=1),
    "no click, slower": dict(tempo_factor=1.37, drift=0.03, header_bpm=120, seed=2),
    "no click, faster": dict(tempo_factor=0.8, drift=0.03, header_bpm=120, seed=3),
}


def notes(stream) -> dict:
    """Onset and pitch -> longest written length (unisons counted once)."""
    found = {}
    for onset, pitch, length in note_events(stream):
        found[(onset, pitch)] = max(found.get((onset, pitch), 0), length)
    return found


def score(truth, reading, shifts) -> tuple:
    best = (0.0, 0.0)
    for shift in shifts:
        moved = {(o - shift, p): d for (o, p), d in reading.items()}
        hits = [k for k in truth if k in moved]
        lengths = sum(truth[k] == moved[k] for k in hits)
        best = max(best, (len(hits) / len(truth), lengths / len(truth)))
    return best


def music21_alone(path) -> dict:
    """What a plain music21 import gives (the usual MIDI-to-notation path)."""
    import music21

    stream = music21.converter.parse(str(path))
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "x.musicxml"
        stream.write("musicxml", fp=str(out))
        return notes(music21.converter.parse(str(out)))


def scoreproof_reading(path) -> tuple:
    with tempfile.TemporaryDirectory() as tmp:
        result = convert(path, Path(tmp) / "x.musicxml")
        return notes(load(result.output).stream), result.interpretation


def main():
    import music21

    rows = []
    with tempfile.TemporaryDirectory() as tmp:
        folder = Path(tmp)
        for name, (ref, bars) in PIECES.items():
            piece = music21.corpus.parse(ref)
            if bars:
                piece = piece.measures(1, bars)
            source = folder / "source.musicxml"
            piece.write("musicxml", fp=str(source))
            exact = convert(source, folder / "exact.mid").output
            truth = notes(load(exact).stream)
            for playing, options in PLAYINGS.items():
                played = perform(exact, folder / "played.mid", **options)
                shifts = [0] if playing == "click" else [k for k in range(-8, 9)]
                plain = score(truth, music21_alone(played), shifts)
                reading, info = scoreproof_reading(played)
                ours = score(truth, reading, shifts)
                rows.append((name, playing, plain, ours, info.get("tempo_bpm")))
                print(
                    f"{name:30} {playing:17} music21 {plain[0]:4.0%} {plain[1]:4.0%}"
                    f"   scoreproof {ours[0]:4.0%} {ours[1]:4.0%}"
                    f"   tempo {info.get('tempo_bpm')}",
                    flush=True,
                )
    print()
    print(
        "| Playing | music21 placed | music21 lengths "
        "| scoreproof placed | scoreproof lengths |"
    )
    print("|---|---|---|---|---|")
    for playing in PLAYINGS:
        mine = [r for r in rows if r[1] == playing]
        mean = lambda i, j: sum(r[i][j] for r in mine) / len(mine)  # noqa: E731
        print(
            f"| {playing} | {mean(2, 0):.0%} | {mean(2, 1):.0%} | "
            f"**{mean(3, 0):.0%}** | **{mean(3, 1):.0%}** |"
        )


if __name__ == "__main__":
    main()
