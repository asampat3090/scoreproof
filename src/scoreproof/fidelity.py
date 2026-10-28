"""Measure what a conversion kept by comparing the source with the re-read output."""

from collections import Counter
from dataclasses import asdict, dataclass

# Onsets and durations are compared on a 1/48-quarter grid, fine enough for
# 64th-note triplets and coarse enough to absorb floating-point noise.
GRID = 48


@dataclass
class Summary:
    parts: int
    measures: int
    notes: int
    duration: float
    lyrics: int
    chord_symbols: int
    dynamics: int
    tempos: int

    def to_dict(self) -> dict:
        return asdict(self)


def _snap(value) -> float:
    return round(float(value) * GRID) / GRID


def note_events(stream) -> Counter:
    """(onset, pitch, duration) for every sounding note, ties merged.

    Pitch is the MIDI number, or the staff position for unpitched
    percussion notes, which have no pitch.
    """
    sounding = stream.toSoundingPitch(inPlace=False)
    events = Counter()
    for part in sounding.parts or [sounding]:
        flat = part.stripTies(inPlace=False).flatten()
        for element in flat.notes:
            if element.duration.isGrace or element.classes[0].endswith("Symbol"):
                continue
            onset, length = _snap(element.offset), _snap(element.quarterLength)
            for single in _members(element):
                events[(onset, _pitch_id(single), length)] += 1
    return events


def _members(element):
    return element.notes if "ChordBase" in element.classes else (element,)


def _pitch_id(note):
    if "Unpitched" in note.classes:
        return f"unpitched {note.displayStep}{note.displayOctave}"
    return note.pitch.midi


def summarize(stream) -> Summary:
    parts = list(stream.parts) or [stream]
    recurse = list(stream.recurse())
    events = note_events(stream)
    return Summary(
        parts=len(parts),
        measures=max(len(p.getElementsByClass("Measure")) for p in parts),
        notes=sum(events.values()),
        # End of the last sounding note: trailing rests that writers add to
        # pad a short final bar are not musical content.
        duration=max((onset + length for onset, _, length in events), default=0.0),
        lyrics=sum(len(n.lyrics) for n in stream.recurse().notes),
        chord_symbols=sum(1 for e in recurse if "ChordSymbol" in e.classes),
        dynamics=sum(1 for e in recurse if "Dynamic" in e.classes),
        tempos=sum(1 for e in recurse if "MetronomeMark" in e.classes),
    )


def expand_repeats(stream):
    """The stream as performed, with repeats written out (if they parse)."""
    try:
        return stream.expandRepeats()
    except Exception:  # malformed repeat marks: compare as written
        return stream


def compare(source, output, performed=False) -> dict:
    """Compare two streams note by note and feature by feature.

    ``performed`` expands the source's repeats first, for targets such as
    MIDI that store the music as played rather than as written.
    """
    if performed:
        source = expand_repeats(source)
    before, after = note_events(source), note_events(output)
    matched = sum((before & after).values())
    src, out = summarize(source), summarize(output)
    recall = matched / sum(before.values()) if before else 1.0
    precision = matched / sum(after.values()) if after else 1.0
    kept = {
        name: _ratio(getattr(out, name), getattr(src, name))
        for name in ("lyrics", "chord_symbols", "dynamics", "tempos")
        if getattr(src, name)
    }
    return {
        "source": src.to_dict(),
        "output": out.to_dict(),
        "note_recall": round(recall, 4),
        "note_precision": round(precision, 4),
        "features_kept": kept,
        "confidence": _confidence(recall, precision, src, out),
    }


def _ratio(after, before) -> float:
    return round(min(after / before, 1.0), 4)


def _confidence(recall, precision, src: Summary, out: Summary) -> str:
    worst = min(recall, precision)
    if worst == 1.0 and abs(src.duration - out.duration) < 1 / GRID:
        return "exact"
    if worst >= 0.99:
        return "high"
    if worst >= 0.95:
        return "medium"
    return "low"
