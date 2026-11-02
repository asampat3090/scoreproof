"""Compact, token-light views of a score: summary, ABC excerpt and checks."""

import tempfile
from pathlib import Path

from scoreproof import fidelity
from scoreproof.errors import ScoreError
from scoreproof.io import load
from scoreproof.paths import input_path, parse_bars

# An excerpt is for reading a passage, not dumping the whole score into an
# AI model's context.
MAX_EXCERPT_BARS = 32
DEFAULT_EXCERPT = "1-8"


def inspect(path, bars=DEFAULT_EXCERPT, parts=None) -> dict:
    """Summarise a score and show the requested bars as ABC notation.

    ``bars=None`` or ``""`` leaves the excerpt out.
    """
    score = load(input_path(path))
    stream = score.stream
    summary = fidelity.summarize(stream)
    info = {
        "file": str(score.source),
        "format": score.format.name,
        "title": _metadata(stream, "title"),
        "composer": _metadata(stream, "composer"),
        "software": score.software,
        "bars": summary.measures,
        "notes": summary.notes,
        "length_quarters": summary.duration,
        "key_signature": key_name(key_signature(stream)),
        "estimated_key": _estimated_key(stream),
        "time_signatures": _time_signatures(stream),
        "tempos": _tempos(stream),
        "parts": [_part_info(i, p) for i, p in enumerate(all_parts(stream), 1)],
        "lyrics": summary.lyrics,
        "chord_symbols": summary.chord_symbols,
        "dynamics": summary.dynamics,
        "repairs": [f.rule for f in score.fixes],
    }
    if bars:
        info["excerpt"] = excerpt(stream, bars, parts)
    return info


def excerpt(stream, bars, parts=None) -> dict:
    """The given bars (and parts) of ``stream`` as ABC text."""
    start, end = parse_bars(bars, bar_count(stream))
    end = min(end, start + MAX_EXCERPT_BARS - 1)
    piece, start, end = subset(stream, f"{start}-{end}", parts)
    with tempfile.TemporaryDirectory() as tmp:
        written = piece.write("abc", fp=str(Path(tmp) / "excerpt.abc"))
        abc = Path(written).read_text()
    return {
        "bars": f"{start}-{end}",
        "parts": [p.partName for p in piece.parts],
        "abc": abc,
    }


def bar_count(stream) -> int:
    return max(len(p.getElementsByClass("Measure")) for p in all_parts(stream))


def subset(stream, bars=None, parts=None):
    """A new score with only the chosen parts and bars (both default to all).

    Returns the score and the inclusive bar range it covers.
    """
    import copy

    import music21

    total = bar_count(stream)
    start, end = parse_bars(bars, total) if bars else (None, None)
    piece = music21.stream.Score()
    for part in select_parts(stream, parts):
        if start is None:
            piece.insert(0, copy.deepcopy(part))
        else:
            piece.insert(0, part.measures(start, end))
    # Writers such as converter21's ABC need metadata; keep the original's.
    if stream.metadata is not None:
        piece.metadata = copy.deepcopy(stream.metadata)
    else:
        piece.metadata = music21.metadata.Metadata()
    first, last = (1, total) if start is None else (start, min(end, total))
    return piece, first, last


def select_parts(stream, parts=None) -> list:
    """Parts chosen by 1-based number or by name (case-insensitive)."""
    available = all_parts(stream)
    if not parts:
        return available
    if isinstance(parts, str):
        parts = [p.strip() for p in parts.split(",") if p.strip()]
    chosen = []
    for wanted in parts:
        text = str(wanted).strip()
        if text.isdigit() and 1 <= int(text) <= len(available):
            chosen.append(available[int(text) - 1])
            continue
        match = [p for p in available if (p.partName or "").lower() == text.lower()]
        if not match:
            names = ", ".join(f"{i} {p.partName}" for i, p in enumerate(available, 1))
            raise ScoreError(f"No part {wanted!r}. Parts: {names}")
        chosen += match
    return chosen


def validate(path) -> dict:
    """Check a score for problems: repaired exporter quirks, bars that do not
    add up, notes outside an instrument's range, and reader warnings."""
    score = load(input_path(path))
    issues = [
        {
            "severity": "repaired",
            "check": f.rule,
            "detail": f.description,
            "count": f.count,
        }
        for f in score.fixes
    ]
    issues += [
        {
            "severity": "warning",
            "check": "irregular_bar",
            "detail": f"Bar {b['measure']} in {b['part']} "
            f"lasts {b['length']:g} quarters; the time signature expects "
            f"{b['expected']:g}",
        }
        for b in score.irregular_bars
    ]
    for part in all_parts(score.stream):
        issues += _range_issues(part)
    issues += [
        {"severity": "info", "check": "reader_warning", "detail": w}
        for w in score.warnings
    ]
    warnings = sum(i["severity"] == "warning" for i in issues)
    status = "warnings" if warnings else "repaired" if score.fixes else "clean"
    return {"file": str(score.source), "status": status, "issues": issues}


def all_parts(stream) -> list:
    """The parts of a score, or the stream itself when it is a single part."""
    return list(getattr(stream, "parts", None) or []) or [stream]


def _metadata(stream, field) -> str:
    md = stream.metadata
    if md is None:
        return ""
    value = md.bestTitle if field == "title" else getattr(md, field, None)
    return str(value) if value else ""


def key_signature(stream) -> str:
    """The first key signature, as a key name such as "F# minor"."""
    for ks in stream.recurse().getElementsByClass("KeySignature"):
        return ks.name if "Key" in ks.classes else ks.asKey().name
    return ""


def key_name(name: str) -> str:
    """music21's "B- major" written the usual way: "Bb major"."""
    return name.replace("-", "b")


def _estimated_key(stream) -> dict:
    if not stream.recurse().notes:
        return {}
    try:
        key = stream.analyze("key")
    except Exception:  # analysis fails on unpitched or degenerate scores
        return {}
    return {
        "key": key_name(key.name),
        "confidence": round(key.correlationCoefficient, 2),
    }


def _time_signatures(stream) -> list:
    seen = []
    for ts in all_parts(stream)[0].recurse().getElementsByClass("TimeSignature"):
        entry = {"bar": ts.measureNumber, "meter": ts.ratioString}
        if not seen or seen[-1]["meter"] != entry["meter"]:
            seen.append(entry)
    return seen


def _tempos(stream) -> list:
    tempos = []
    for mark in all_parts(stream)[0].recurse().getElementsByClass("MetronomeMark"):
        # A tempo set only as playback speed (<sound tempo>) has no written
        # number, just a sounding one.
        bpm = mark.number if mark.number is not None else mark.numberSounding
        if bpm is None and not mark.text:
            continue
        tempos.append(
            {
                "bar": mark.measureNumber,
                "bpm": bpm,
                "text": mark.text or "",
            }
        )
    return tempos


def _part_info(number, part) -> dict:
    pitches = [p for n in part.recurse().notes for p in getattr(n, "pitches", ())]
    instrument = part.getInstrument(returnDefault=False)
    info = {
        "number": number,
        "name": part.partName or f"Part {number}",
        "instrument": instrument.instrumentName if instrument else "",
        "notes": len(pitches),
    }
    if pitches:
        low, high = min(pitches), max(pitches)
        info["range"] = f"{low.nameWithOctave}-{high.nameWithOctave}"
    return info


def _range_issues(part) -> list:
    instrument = _instrument_with_range(part)
    if instrument is None:
        return []
    low, high = instrument.lowestNote, instrument.highestNote
    found = {}
    sounding = part.toSoundingPitch(inPlace=False)
    for note in sounding.recurse().notes:
        for pitch in getattr(note, "pitches", ()):
            if low and pitch < low:
                problem = f"below the lowest {instrument.instrumentName} note ({low})"
            elif high and pitch > high:
                problem = f"above the highest {instrument.instrumentName} note ({high})"
            else:
                continue
            key = (pitch.nameWithOctave, problem)
            found.setdefault(key, set()).add(note.measureNumber)
    return [
        {
            "severity": "warning",
            "check": "out_of_range",
            "detail": f"{name} is {problem} in bars "
            + ", ".join(str(b) for b in sorted(b for b in bars if b is not None)),
        }
        for (name, problem), bars in sorted(found.items())
    ]


def _instrument_with_range(part):
    """The part's instrument with known range limits, looked up by name when
    the file only names it (e.g. "Flute")."""
    import music21

    instrument = part.getInstrument(returnDefault=False)
    if instrument is None:
        return None
    if not (instrument.lowestNote or instrument.highestNote):
        try:
            instrument = music21.instrument.fromString(instrument.instrumentName or "")
        except music21.instrument.InstrumentException:
            return None
    if not (instrument.lowestNote or instrument.highestNote):
        return None
    return instrument


def compare(a, b) -> dict:
    """What changed between two versions of a piece, overall and bar by bar.

    The files may be in different formats.
    """
    first, second = load(input_path(a)), load(input_path(b))
    report = fidelity.compare(first.stream, second.stream)
    changes = fidelity.bar_differences(first.stream, second.stream)
    return {
        "a": str(first.source),
        "b": str(second.source),
        "identical_notes": report["note_recall"] == report["note_precision"] == 1.0,
        "notes_of_a_in_b": report["note_recall"],
        "notes_of_b_in_a": report["note_precision"],
        "a_summary": report["source"],
        "b_summary": report["output"],
        "changed_bars": changes,
    }
