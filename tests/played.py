"""Make 'played' MIDI from exact MIDI, the way a person at a keyboard would:
one piano track, starts a little early or late, notes released early or
held into the next, uneven velocities, and optionally no click at all."""

import math
import random

import pretty_midi


def perform(source, target, tempo_factor=1.0, drift=0.0, header_bpm=None, seed=0):
    """Write a played version of ``source``.

    ``tempo_factor`` stretches time (1.37 is 37% slower) and ``drift`` makes
    the beat wander by that fraction; ``header_bpm`` is the tempo the file
    claims, which for a free performance has nothing to do with the playing.
    """
    rng = random.Random(seed)
    exact = pretty_midi.PrettyMIDI(str(source))
    bpm = exact.get_tempo_changes()[1][0]
    notes = [n for inst in exact.instruments if not inst.is_drum for n in inst.notes]
    end = max(exact.time_to_tick(n.end) for n in notes) / exact.resolution

    def seconds(beat):
        wave = 4 * math.pi / end
        played = beat + drift / wave * (1 - math.cos(wave * beat))
        return played * 60 / bpm * tempo_factor

    out = pretty_midi.PrettyMIDI(initial_tempo=header_bpm or bpm)
    out.time_signature_changes = exact.time_signature_changes[:1]
    piano = pretty_midi.Instrument(0, name="Piano")
    for n in notes:
        first = exact.time_to_tick(n.start) / exact.resolution
        last = exact.time_to_tick(n.end) / exact.resolution
        start = max(0.0, seconds(first) + rng.gauss(0, 0.015))
        length = (seconds(last) - seconds(first)) * rng.uniform(0.75, 1.08)
        velocity = int(min(127, max(30, rng.gauss(80, 12))))
        piano.notes.append(
            pretty_midi.Note(velocity, n.pitch, start, start + max(0.03, length))
        )
    out.instruments.append(piano)
    out.write(str(target))
    return target
