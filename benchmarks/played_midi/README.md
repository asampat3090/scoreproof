# Played MIDI benchmark

MIDI recorded in a DAW or from a keyboard does not sit on the beat grid.
This benchmark measures how much of the written music comes back when such
a file is read as notation.

Five pieces from the music21 corpus are exported to exact MIDI, then
"performed" by `tests/played.py`:
- one piano track
- note starts up to about 30 ms early or late
- notes released early or held into the next
- uneven velocities

They are played three ways: with a click; and twice with no click, slower
and faster than written, the beat drifting by 3%, and a file that claims
120 bpm. Each reading is compared with the exact notes:

- **placed**: notes at the right beat with the right pitch
- **lengths**: notes that also have the right written length

```bash
python benchmarks/played_midi/run.py
```

## Results (2026-11-02)

| Playing | music21 placed | music21 lengths | scoreproof placed | scoreproof lengths |
|---|---|---|---|---|
| click | 64% | 43% | **99%** | **84%** |
| no click, slower | 6% | 1% | **81%** | **67%** |
| no click, faster | 21% | 10% | **82%** | **67%** |

Without a click, 8 of the 10 readings find the written beat; the other two
(the rag played slowly, the Schumann played fast) are read at double or
half speed: the notes are right but every length is doubled or halved,
which this benchmark counts as wrong. Length errors that remain are mostly
notes held under moving voices, which a single staff per hand shows with
ties, and unisons between voices that one piano key cannot play twice.
