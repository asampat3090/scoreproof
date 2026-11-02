"""Everyday score chores: transposing and pulling out parts or bars."""

import re

from scoreproof import fidelity
from scoreproof.analysis import (
    all_parts,
    key_name,
    key_signature,
    select_parts,
    subset,
)
from scoreproof.errors import ScoreError
from scoreproof.formats import get_format
from scoreproof.io import Score, load
from scoreproof.paths import input_path, output_path

# Spellings to try for a transposition given in semitones; the one that
# gives the simplest key signature wins (up 1 into F# major: G major, not
# F## major).
_SPELLINGS = {
    0: ("P1",),
    1: ("m2", "A1"),
    2: ("M2", "d3"),
    3: ("m3", "A2"),
    4: ("M3", "d4"),
    5: ("P4", "A3"),
    6: ("A4", "d5"),
    7: ("P5", "d6"),
    8: ("m6", "A5"),
    9: ("M6", "d7"),
    10: ("m7", "A6"),
    11: ("M7", "d8"),
}


def transpose(path, by=None, to_key=None, pitch=None, output=None, to=None):
    """Transpose a score and save it as a new file.

    Give one of:
      ``by``: an interval ("M2", "-m3", "P5") or semitones ("2", "-3")
      ``to_key``: a target key ("D", "Bb major", "F# minor")
      ``pitch``: "concert" (sounding pitch) or "written" (transposed parts)
    """
    if sum(x is not None for x in (by, to_key, pitch)) != 1:
        raise ScoreError("Give exactly one of: an interval, a target key, or pitch")
    score = load(input_path(path))
    stream = score.stream
    before = key_signature(stream)
    if pitch is not None:
        result = _to_pitch(stream, pitch)
        label = f"{pitch} pitch"
        suffix = f"-{pitch}"
    else:
        step = _interval_to_key(before, to_key) if to_key else _interval(by, before)
        result = stream.transpose(step)
        label = step.directedName
        suffix = "-" + _slug(key_name(key_signature(result)) or label)
    target = get_format(to) if to else score.format
    destination = output_path(score.source, target.extension, output, suffix)
    written = Score(result, target).save(destination, target.name)
    result_info = {
        "output": str(written),
        "transposition": label,
        "from_key": key_name(before),
        "to_key": key_name(key_signature(result)),
        "notes": fidelity.summarize(result).notes,
    }
    if to_key and before and _split_key(to_key)[1] != _split_key(before)[1]:
        result_info["note"] = (
            f"The music is in {key_name(before)}, so the mode was kept: "
            f"it now has the tonic you asked for, in {result_info['to_key']}"
        )
    return result_info


def extract(path, parts=None, bars=None, each_part=False, output=None, to=None):
    """Save chosen parts and/or bars of a score as new file(s).

    ``each_part`` writes one file per chosen part, the usual way to make
    parts for players.
    """
    score = load(input_path(path))
    target = get_format(to) if to else score.format
    stream = score.stream
    if each_part:
        chosen = select_parts(stream, parts)
        numbers = [
            str(n)
            for n, part in enumerate(all_parts(stream), 1)
            if any(part is c for c in chosen)
        ]
        groups = [[n] for n in numbers]
    else:
        groups = [parts]
    files = []
    for group in groups:
        piece, start, end = subset(stream, bars, group)
        names = [p.partName or "part" for p in piece.parts]
        suffix = _suffix(names if (group or each_part) else [], bars, start, end)
        destination = output_path(score.source, target.extension, output, suffix)
        written = Score(piece, target).save(destination, target.name)
        files.append(
            {
                "output": str(written),
                "parts": names,
                "bars": f"{start}-{end}" if bars else "all",
                "notes": fidelity.summarize(piece).notes,
            }
        )
    return {"files": files}


def _slug(text) -> str:
    text = text.replace("#", "sharp").replace(" ", "-")
    return re.sub(r"[^\w-]+", "", text).strip("-").lower()


def _suffix(names, bars, start, end) -> str:
    bits = [_slug(n) for n in names]
    if bars:
        bits.append(f"bars-{start}-{end}")
    return "-" + "-".join(bits) if bits else "-extract"


def _to_pitch(stream, pitch):
    if pitch == "concert":
        return stream.toSoundingPitch(inPlace=False)
    if pitch == "written":
        return stream.toWrittenPitch(inPlace=False)
    raise ScoreError(f"pitch must be 'concert' or 'written', not {pitch!r}")


def _interval(by, key_name):
    from music21 import interval

    text = str(by).strip()
    if re.fullmatch(r"[+-]?\d+", text):
        return _semitone_interval(int(text), key_name)
    try:
        return interval.Interval(text)
    except Exception:
        raise ScoreError(
            f"Not an interval: {by!r}. Use names like M2, -m3, P5 or semitones"
        ) from None


def _semitone_interval(semitones, key_name):
    from music21 import interval, key, pitch

    octaves, rest = divmod(abs(semitones), 12)
    start = pitch.Pitch("C4")
    candidates = []
    for name in _SPELLINGS[rest]:
        end = start.transpose(name)
        for _ in range(octaves):
            end = end.transpose("P8")
        step = interval.Interval(start, end)
        candidates.append(step.reverse() if semitones < 0 else step)
    if not key_name:
        return candidates[0]
    sharps = key.Key(*_split_key(key_name)).sharps
    return min(candidates, key=lambda s: abs(_transposed_sharps(sharps, s)))


def _transposed_sharps(sharps, step):
    from music21 import key

    return key.KeySignature(sharps).transpose(step).sharps


def _interval_to_key(current, to_key):
    from music21 import interval, key, pitch

    if not current:
        raise ScoreError("The score has no key signature to transpose from")
    source = key.Key(*_split_key(current))
    target = key.Key(*_split_key(to_key))
    start = pitch.Pitch(source.tonic.name + "4")
    end = pitch.Pitch(target.tonic.name + "4")
    step = interval.Interval(start, end)
    # Move by the smaller distance, up or down.
    if step.semitones > 6:
        end.octave -= 1
    elif step.semitones < -6:
        end.octave += 1
    return interval.Interval(start, end)


def _split_key(text):
    """("B-", "major") from "Bb", "Bb major", "bb minor", "Bbm" or "B- major"."""
    words = str(text).strip().split()
    if not words:
        raise ScoreError("Empty key name")
    tonic = words[0]
    mode = words[1].lower() if len(words) > 1 else "major"
    if len(words) == 1 and len(tonic) > 1 and tonic.endswith("m"):
        tonic, mode = tonic[:-1], "minor"
    if not re.fullmatch(r"[A-Ga-g](#{1,2}|b{1,2}|-{1,2})?", tonic):
        raise ScoreError(f"Not a key: {text!r}. Try 'D', 'Bb major' or 'F# minor'")
    if mode not in ("major", "minor"):
        raise ScoreError(f"Mode must be major or minor, not {mode!r}")
    return tonic[0].upper() + tonic[1:].replace("b", "-"), mode
