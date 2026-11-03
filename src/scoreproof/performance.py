"""Turn MIDI that someone played (in a DAW, on a keyboard) into readable notation.

MIDI exported from a notation app sits exactly on the beat grid and needs no
help. Played MIDI does not: notes start a little early or late, overlap the
next note, stop short, and with no click there is no reliable beat at all.
Read naively, that becomes 64th-note rests, ties and tuplets everywhere.

``interpret`` finds the beat (from the file's tempo map when the player used
a click, otherwise by tracking it), snaps each beat to straight or triplet
subdivisions, tidies note lengths, splits keyboard parts across two staves,
and spells notes in the detected key.
"""

import math
from bisect import bisect_right
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path

from scoreproof.errors import ScoreError

# A note this close to the 1/48-beat grid counts as written, not played.
EXACT = 0.05 / 48
# Share of off-grid notes above which a file counts as played.
PLAYED_SHARE = 0.1
TRIPLET_PENALTY = 1.5
# Gaps shorter than this many beats (or a quarter of the time to the next
# note) are read as the player lifting early, not as a written rest.
GAP_FILL = 0.5
# A note that runs into the next one by less than this share of its length
# is played legato; a bigger overlap is a note held under moving voices.
LEGATO = 0.25
SPLIT_PITCH = 60  # middle C
HAND_SPAN = 14  # semitones one hand can cover in a chord
FRAMES_PER_SECOND = 100


@dataclass
class PlayedNote:
    start: float  # beats
    end: float
    pitch: int
    velocity: int
    track: int
    seconds: float


def is_played(midi) -> bool:
    """True when the notes do not sit on the beat grid (onsets or ends)."""
    times = [t for inst in _pitched(midi) for n in inst.notes for t in (n.start, n.end)]
    if not times:
        return False
    beats = [midi.time_to_tick(t) / midi.resolution for t in times]
    off = sum(abs(b * 48 - round(b * 48)) > EXACT * 48 for b in beats)
    return off / len(beats) > PLAYED_SHARE


def interpret(path) -> tuple:
    """Read played MIDI as notation: (music21 score, what was decided)."""
    import pretty_midi

    midi = pretty_midi.PrettyMIDI(str(path))
    instruments = _pitched(midi)
    if not any(inst.notes for inst in instruments):
        raise ScoreError("This MIDI file has no pitched notes")
    beat_times, tracked = _beats(midi)
    notes = []
    for track, inst in enumerate(instruments):
        for n in inst.notes:
            notes.append(
                PlayedNote(
                    _beat_position(n.start, beat_times),
                    _beat_position(n.end, beat_times),
                    n.pitch,
                    n.velocity,
                    track,
                    n.start,
                )
            )
    beats_per_bar, unit = _meter(midi)
    # A tracked beat in 6/8, 9/8 or 12/8 is a dotted quarter.
    per_beat = 1.5 if tracked and unit[1] == 8 and unit[0] % 3 == 0 else 1
    shift = _align_downbeat(notes, beats_per_bar / per_beat) if tracked else 0
    for n in notes:
        n.start, n.end = n.start * per_beat, n.end * per_beat
    grid = _snap(notes)
    lead = _drop_empty_bars(notes, Fraction(beats_per_bar).limit_denominator(12))
    staves = _staves(notes, instruments)
    for staff in staves.values():
        _tidy_lengths(staff["notes"])
    key = _key(midi, notes)
    tempo = _tempo(midi, beat_times, tracked)
    score = _build(staves, key, beats_per_bar, unit, tempo, Path(path).stem)
    moved = _timing_moved(notes, beat_times, lambda q: (q + lead) / per_beat - shift)
    summary = {
        "played_midi": True,
        "beat": "tracked (no click)" if tracked else "from the file's tempo map",
        "tempo_bpm": round(tempo),
        "grid": grid,
        "key": f"{key.tonic.name.replace('-', 'b')} {key.mode}",
        "staves": [s["name"] for s in staves.values()],
        "timing_moved_ms": moved,
    }
    drums = sum(len(i.notes) for i in midi.instruments if i.is_drum)
    if drums:
        summary["left_out"] = f"{drums} drum notes (drum notation is not supported)"
    return score, summary


def _pitched(midi) -> list:
    return [inst for inst in midi.instruments if not inst.is_drum and inst.notes]


# --- Finding the beat -------------------------------------------------------


def _beats(midi) -> tuple:
    """Beat times in seconds, and whether they had to be tracked.

    A player who used a click lines up with the file's own beats; a free
    performance only lines up by chance, so its beat is tracked instead.
    """
    onsets = sorted(n.start for inst in _pitched(midi) for n in inst.notes)
    end = max(n.end for inst in _pitched(midi) for n in inst.notes)
    # Quarter notes from the tempo map (pretty_midi's own beats follow the
    # time signature, e.g. dotted quarters in 6/8).
    file_beats = []
    while not file_beats or file_beats[-1] < end + 2:
        file_beats.append(midi.tick_to_time(len(file_beats) * midi.resolution))
    if _fits_grid(onsets, file_beats):
        return file_beats, False
    return _track_beats(onsets, end), True


def _fits_grid(onsets, beat_times) -> bool:
    """Did the player follow this beat grid (a click)?

    Onsets fit a real click best at exactly its tempo, clearly better than
    at slightly faster or slower ones. A free performance fits any nearby
    grid about equally badly, so its best fit lands somewhere at random.
    """
    first = beat_times[0]
    misfits = []
    for step in range(-30, 31):  # tempo scales 0.85 to 1.15
        scale = 1 + step * 0.005
        scaled = [first + (t - first) * scale for t in beat_times]
        misfits.append(sum(_grid_error(_beat_position(t, scaled)) for t in onsets))
    best = misfits.index(min(misfits)) - 30
    typical = sorted(misfits)[len(misfits) // 2]
    return abs(best) <= 1 and misfits[30] < 0.9 * typical


def _grid_error(position) -> float:
    fraction = position - math.floor(position)
    return min(abs(fraction * s - round(fraction * s)) / s for s in (4, 3))


def _track_beats(onsets, end) -> list:
    """Dynamic-programming beat tracking (Ellis 2007) on note onsets."""
    import numpy as np

    frames = int((end + 1) * FRAMES_PER_SECOND) + 1
    envelope = np.zeros(frames)
    for t in onsets:
        envelope[int(round(t * FRAMES_PER_SECOND))] += 1
    kernel = np.exp(-0.5 * (np.arange(-6, 7) / 2.0) ** 2)
    envelope = np.convolve(envelope, kernel, mode="same")
    period = _beat_period(envelope)
    score = envelope.copy()
    backlink = np.full(frames, -1)
    window = np.arange(-2 * period, -(period // 2) + 1)
    penalty = -100 * np.log(-window / period) ** 2
    for t in range(frames):
        candidates = t + window
        valid = candidates >= 0
        if not valid.any():
            continue
        values = score[candidates[valid]] + penalty[valid]
        best = int(np.argmax(values))
        score[t] += values[best]
        backlink[t] = candidates[valid][best]
    beat = int(np.argmax(score[-period:])) + frames - period
    beats = []
    while beat >= 0:
        beats.append(beat / FRAMES_PER_SECOND)
        beat = backlink[beat]
    beats.reverse()
    step = period / FRAMES_PER_SECOND
    while beats[0] > onsets[0] - step / 2:
        beats.insert(0, beats[0] - step)
    while beats[-1] < end + step:
        beats.append(beats[-1] + step)
    return beats


def _beat_period(envelope) -> int:
    """Beat length in frames: strongest periodicity, favouring ~110 bpm."""
    import numpy as np

    lags = np.arange(int(FRAMES_PER_SECOND * 60 / 200), int(FRAMES_PER_SECOND * 1.5))
    centred = envelope - envelope.mean()
    strength = np.array([np.dot(centred[:-lag], centred[lag:]) for lag in lags])
    bpm = 60 * FRAMES_PER_SECOND / lags
    prior = np.exp(-0.5 * (np.log2(bpm / 110) / 0.9) ** 2)
    return int(lags[int(np.argmax(np.maximum(strength, 0) * prior))])


def _beat_position(seconds, beat_times) -> float:
    """Fractional beat number of a time, interpolating between beats."""
    index = min(max(bisect_right(beat_times, seconds) - 1, 0), len(beat_times) - 2)
    start, end = beat_times[index], beat_times[index + 1]
    return index + (seconds - start) / (end - start)


def _meter(midi) -> tuple:
    """Beats per bar and beat unit from the file (4/4 when absent)."""
    if midi.time_signature_changes:
        sig = midi.time_signature_changes[0]
        return sig.numerator * 4 / sig.denominator, (sig.numerator, sig.denominator)
    return 4, (4, 4)


def _align_downbeat(notes, beats_per_bar) -> int:
    """Without a click, put bar lines where the music leans: the beat with
    the most and lowest notes starts the bar. Returns the shift in beats."""
    bar = max(round(beats_per_bar), 1)
    weight = [0.0] * bar
    for n in notes:
        nearest = round(n.start)
        if abs(n.start - nearest) < 0.15:
            weight[nearest % bar] += 1 + max(0, 60 - n.pitch) / 24
    shift = (bar - max(range(bar), key=lambda b: weight[b])) % bar
    for n in notes:
        n.start += shift
        n.end += shift
    return shift


def _drop_empty_bars(notes, bar) -> Fraction:
    """Remove whole empty bars before the first note (a count-in, say)."""
    lead = math.floor(min(n.start for n in notes) / bar) * bar
    for n in notes:
        n.start -= lead
        n.end -= lead
    return lead


# --- Rhythm -----------------------------------------------------------------


def _snap(notes) -> str:
    """Snap onsets beat by beat to 16ths or to triplets, whichever fits.

    Choosing per beat keeps a stray early note in a straight passage from
    turning into a triplet. Returns a description of the grid used.
    """
    by_beat = {}
    for n in notes:
        by_beat.setdefault(math.floor(n.start + 1 / 8), []).append(n)
    triplet_beats = set()
    for beat, members in by_beat.items():
        offsets = [n.start - beat for n in members]
        straight = sum(abs(o * 4 - round(o * 4)) / 4 for o in offsets)
        triplet = sum(abs(o * 3 - round(o * 3)) / 3 for o in offsets)
        if triplet * TRIPLET_PENALTY + 0.01 < straight:
            triplet_beats.add(beat)
    for n in notes:
        n.start = _round(n.start, triplet_beats)
        n.end = max(
            _round(n.end, triplet_beats), n.start + _step(n.start, triplet_beats)
        )
    if triplet_beats:
        return "16th notes and triplets"
    return "16th notes"


def _division(position, triplet_beats) -> int:
    return 3 if math.floor(position + 1 / 8) in triplet_beats else 4


def _round(position, triplet_beats) -> Fraction:
    division = _division(position, triplet_beats)
    return Fraction(round(position * division), division)


def _step(position, triplet_beats) -> Fraction:
    return Fraction(1, _division(position, triplet_beats))


def _tidy_lengths(notes):
    """Line releases up with the notes around them.

    A release just before or after another note's start is the player
    lifting early or playing legato, so it moves to that start. A key
    struck again always ends the earlier note.
    """
    onsets = sorted({n.start for n in notes})
    restruck = {}
    for n in sorted(notes, key=lambda n: n.start, reverse=True):
        n.end = min(n.end, restruck.get(n.pitch, n.end))
        restruck[n.pitch] = n.start
    for n in notes:
        length = n.end - n.start
        nearby = [
            o
            for o in onsets[bisect_right(onsets, n.start) :]
            if n.end - LEGATO * length < o and _fills(o - n.end, o - n.start)
        ]
        if nearby:
            n.end = min(nearby, key=lambda o: (abs(o - n.end), -o))


def _fills(gap, span) -> bool:
    """Is a silence of ``gap`` beats before a note ``span`` beats after the
    last start too short to be a written rest? (Negative gaps overlap.)"""
    return gap < GAP_FILL or gap <= span / 4


def _timing_moved(notes, beat_times, to_beats) -> dict:
    """How far snapping moved note starts, in milliseconds."""
    moves = sorted(
        abs(_seconds(to_beats(float(n.start)), beat_times) - n.seconds) * 1000
        for n in notes
    )
    return {"median": round(moves[len(moves) // 2]), "max": round(moves[-1])}


def _seconds(position, beat_times) -> float:
    index = min(max(math.floor(position), 0), len(beat_times) - 2)
    start, end = beat_times[index], beat_times[index + 1]
    return start + (position - index) * (end - start)


# --- Layout -----------------------------------------------------------------


def _staves(notes, instruments) -> dict:
    """One staff per track; keyboard-like tracks get one per hand."""
    staves = {}
    for track, inst in enumerate(instruments):
        own = [n for n in notes if n.track == track]
        name = inst.name.strip() or _program_name(inst.program)
        if _needs_two_staves(own, inst.program):
            upper, lower = _hands(own)
            staves[(track, 0)] = _staff(
                f"{name} (right hand)", name, upper, "treble", inst, track
            )
            staves[(track, 1)] = _staff(
                f"{name} (left hand)", name, lower, "bass", inst, track
            )
        else:
            staves[(track, 0)] = _staff(name, name, own, None, inst, None)
    return staves


def _staff(label, inst_name, notes, clef, inst, group) -> dict:
    return {
        "name": label,
        "inst_name": inst_name,
        "notes": notes,
        "clef": clef,
        "inst": inst,
        "group": group,
    }


def _hands(notes) -> tuple:
    """Split a keyboard part into right and left hand.

    Notes struck together stay together unless they span more than one hand
    can reach, in which case they split at the widest gap. Otherwise each
    chord goes to the hand whose recent notes are closest, so a left-hand
    chord reaching above middle C stays in the left hand.
    """
    centre = {"right": SPLIT_PITCH + 7.0, "left": SPLIT_PITCH - 8.0}
    hands = {"right": [], "left": []}
    groups = {}
    for n in notes:
        groups.setdefault(n.start, []).append(n)
    for start in sorted(groups):
        group = sorted(groups[start], key=lambda n: n.pitch)
        if group[-1].pitch - group[0].pitch > HAND_SPAN:
            gaps = [b.pitch - a.pitch for a, b in zip(group, group[1:])]
            cut = gaps.index(max(gaps)) + 1
            parts = [("left", group[:cut]), ("right", group[cut:])]
        else:
            mean = sum(n.pitch for n in group) / len(group)
            hand = min(centre, key=lambda h: abs(centre[h] - mean))
            parts = [(hand, group)]
        for hand, members in parts:
            hands[hand] += members
            mean = sum(n.pitch for n in members) / len(members)
            centre[hand] = 0.7 * centre[hand] + 0.3 * mean
    return hands["right"], hands["left"]


def _needs_two_staves(notes, program) -> bool:
    low = sum(n.pitch < SPLIT_PITCH for n in notes)
    high = len(notes) - low
    keyboard = program < 8 or 16 <= program < 24
    wide = min(low, high) >= max(2, len(notes) // 10)
    return wide and (
        keyboard or max(n.pitch for n in notes) - min(n.pitch for n in notes) > 24
    )


def _program_name(program) -> str:
    import pretty_midi

    return pretty_midi.program_to_instrument_name(program)


# --- Key, tempo and the score -----------------------------------------------


def _key(midi, notes):
    """The key from the file when it has one, otherwise estimated."""
    from music21 import key, stream
    from music21 import note as m21note

    if midi.key_signature_changes:
        number = midi.key_signature_changes[0].key_number
        tonic = ["C", "C#", "D", "E-", "E", "F", "F#", "G", "A-", "A", "B-", "B"]
        if number < 12:
            return key.Key(tonic[number])
        return key.Key(tonic[number - 12].lower())
    sample = stream.Stream()
    for n in notes:
        sample.append(m21note.Note(n.pitch, quarterLength=float(n.end - n.start)))
    return sample.analyze("key")


def _tempo(midi, beat_times, tracked) -> float:
    if not tracked:
        return float(midi.get_tempo_changes()[1][0])
    lengths = sorted(b - a for a, b in zip(beat_times, beat_times[1:]))
    return 60 / lengths[len(lengths) // 2]


def _spell(midi_number, key):
    """A pitch spelled for the key: scale notes as in the key signature,
    the raised 6th and 7th in minor, otherwise sharps in sharp keys and
    flats in flat keys."""
    from music21 import pitch

    pc = midi_number % 12
    names = {p.pitchClass: p.name for p in key.getScale().getPitches()}
    if key.mode == "minor":
        for degree in (6, 7):
            raised = key.pitchFromDegree(degree).transpose(1)
            names.setdefault(raised.pitchClass, raised.name)
    if pc not in names:
        sharps = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]
        flats = ["C", "D-", "D", "E-", "E", "F", "G-", "G", "A-", "A", "B-", "B"]
        default = ["C", "C#", "D", "E-", "E", "F", "F#", "G", "G#", "A", "B-", "B"]
        table = sharps if key.sharps > 0 else flats if key.sharps < 0 else default
        names[pc] = table[pc]
    spelled = pitch.Pitch(names[pc])
    spelled.octave = 4
    spelled.octave += (midi_number - spelled.midi) // 12
    return spelled


def _build(staves, key, beats_per_bar, unit, tempo, title):
    """A music21 score with one part per staff, braced for keyboards."""
    from music21 import clef, instrument, layout, metadata, meter, stream
    from music21 import tempo as m21tempo
    from music21.stream import makeNotation

    score = stream.Score()
    score.metadata = metadata.Metadata(title=title)
    end = max((n.end for s in staves.values() for n in s["notes"]), default=Fraction(0))
    bar = Fraction(beats_per_bar).limit_denominator(12)
    end = math.ceil(end / bar) * bar
    groups = {}
    for number, staff in enumerate(staves.values()):
        part = stream.PartStaff() if staff["group"] is not None else stream.Part()
        # Both hands of a keyboard share its name, as in printed music.
        name = staff["inst_name"]
        part.partName = name
        part.id = f"P{number + 1}"
        part.insert(0, meter.TimeSignature(f"{unit[0]}/{unit[1]}"))
        part.insert(0, key)
        if number == 0:
            part.insert(0, m21tempo.MetronomeMark(number=round(tempo)))
        for element in _segments(staff["notes"], key, end):
            part.append(element)
        part.makeMeasures(inPlace=True)
        # After makeMeasures, so the instrument stays on the part where
        # music21's getInstrument (and MusicXML readers) look for it.
        inst = instrument.instrumentFromMidiProgram(staff["inst"].program)
        inst.partName = name
        part.insert(0, inst)
        if staff["clef"]:
            _hand_clefs(part, staff["clef"])
        else:
            part.getElementsByClass("Measure")[0].insert(0, clef.bestClef(part))
        part.makeTies(inPlace=True)
        makeNotation.makeAccidentalsInMeasureStream(part, useKeySignature=key)
        part.makeBeams(inPlace=True)
        score.insert(0, part)
        if staff["group"] is not None:
            groups.setdefault(staff["group"], []).append(part)
    for members in groups.values():
        score.insert(0, layout.StaffGroup(members, symbol="brace", barTogether=True))
    return score


def _hand_clefs(part, start):
    """Treble or bass clef bar by bar, following where the hand plays.

    A clef changes only when a bar sits clearly in the other register, so
    the staff does not flip back and forth around middle C.
    """
    from music21 import clef

    current = None
    for measure in part.getElementsByClass("Measure"):
        pitches = [p.midi for n in measure.notes for p in n.pitches]
        wanted = current or start
        if pitches:
            middle = sum(pitches) / len(pitches)
            if middle >= SPLIT_PITCH + 3:
                wanted = "treble"
            elif middle <= SPLIT_PITCH - 3:
                wanted = "bass"
        if wanted != current:
            measure.insert(
                0, clef.TrebleClef() if wanted == "treble" else clef.BassClef()
            )
            current = wanted


def _segments(notes, key, end) -> list:
    """Notes, chords and rests that never overlap: wherever the set of
    sounding notes changes a new chord starts, and a note held across the
    change continues as a tie."""
    from music21 import chord, note as m21note

    times = sorted(
        {Fraction(0), end} | {n.start for n in notes} | {n.end for n in notes}
    )
    elements = []
    for start, stop in zip(times, times[1:]):
        sounding = sorted(
            (n for n in notes if n.start <= start < n.end), key=lambda n: n.pitch
        )
        length = stop - start
        if not sounding:
            elements.append(m21note.Rest(quarterLength=length))
            continue
        pitches = []
        for n in sounding:
            if n.pitch not in [p.midi for p in pitches]:
                pitches.append(_spell(n.pitch, key))
        element = (
            m21note.Note(pitches[0], quarterLength=length)
            if len(pitches) == 1
            else chord.Chord(pitches, quarterLength=length)
        )
        for n in sounding:
            if n.start < start:  # held over from the previous segment
                _tie(elements[-1], n.pitch, "start")
                _tie(element, n.pitch, "stop")
        element.volume.velocity = max(n.velocity for n in sounding)
        elements.append(element)
    return elements


def _tie(element, midi_number, kind):
    """Add a tie start or stop to one pitch of a note or chord."""
    from music21 import tie

    target = next(p for p in element.pitches if p.midi == midi_number)
    current = element.getTie(target) if element.isChord else element.tie
    if current is not None and current.type != kind:
        kind = "continue"
    if element.isChord:
        element.setTie(tie.Tie(kind), target)
    else:
        element.tie = tie.Tie(kind)
