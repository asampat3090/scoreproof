"""Tests for reading MIDI that someone played as readable notation."""

from collections import Counter
from fractions import Fraction

import pretty_midi
import pytest
from played import perform

from scoreproof import convert, load, performance
from scoreproof.analysis import inspect
from scoreproof.errors import ScoreError
from scoreproof.fidelity import note_events


def starts(stream) -> Counter:
    """(onset, pitch) of every note, unisons counted once (one keyboard)."""
    return Counter({(onset, pitch): 1 for onset, pitch, _ in note_events(stream)})


def placed(exact, played, shift=0) -> float:
    """Share of the exact notes found at the same beat in the played reading."""
    truth = starts(load(exact).stream)
    found = Counter({(o - shift, p): 1 for o, p in starts(played.stream)})
    return sum((truth & found).values()) / sum(truth.values())


@pytest.fixture
def exact_midi(chorale_file):
    return convert(chorale_file, to="midi").output


@pytest.fixture
def with_click(exact_midi, tmp_path):
    return perform(exact_midi, tmp_path / "with-click.mid", seed=1)


@pytest.fixture
def no_click(exact_midi, tmp_path):
    return perform(
        exact_midi,
        tmp_path / "no-click.mid",
        tempo_factor=1.37,
        drift=0.03,
        header_bpm=120,
        seed=2,
    )


def write_midi(path, notes, program=0, name="", tempo=120, drums=(), **meta):
    """A MIDI file from (start, end, pitch) tuples in seconds."""
    midi = pretty_midi.PrettyMIDI(initial_tempo=tempo)
    inst = pretty_midi.Instrument(program, name=name)
    inst.notes = [pretty_midi.Note(80, p, s, e) for s, e, p in notes]
    midi.instruments.append(inst)
    if drums:
        kit = pretty_midi.Instrument(0, is_drum=True)
        kit.notes = [pretty_midi.Note(80, 36, s, s + 0.1) for s in drums]
        midi.instruments.append(kit)
    if "key" in meta:
        midi.key_signature_changes = [pretty_midi.KeySignature(meta["key"], 0)]
    if "meter" in meta:
        midi.time_signature_changes = [pretty_midi.TimeSignature(*meta["meter"], 0)]
    midi.write(str(path))
    return path


def test_exported_midi_is_left_alone(exact_midi):
    score = load(exact_midi)
    assert score.interpretation == {}
    assert not performance.is_played(pretty_midi.PrettyMIDI(str(exact_midi)))


def test_empty_midi_is_not_played():
    assert not performance.is_played(pretty_midi.PrettyMIDI())


def test_played_with_a_click(exact_midi, with_click):
    score = load(with_click)
    info = score.interpretation
    assert info["played_midi"] is True
    assert info["beat"] == "from the file's tempo map"
    assert info["tempo_bpm"] == 96
    assert info["key"] == "F# minor"
    assert info["staves"] == ["Piano (right hand)", "Piano (left hand)"]
    assert info["timing_moved_ms"]["median"] < 30
    assert placed(exact_midi, score) >= 0.98
    # Both hands carry the instrument's name, as in printed music.
    assert [p.partName for p in score.stream.parts] == ["Piano", "Piano"]


def test_played_without_a_click(exact_midi, no_click):
    score = load(no_click)
    info = score.interpretation
    assert info["beat"] == "tracked (no click)"
    assert 66 <= info["tempo_bpm"] <= 74  # played at 96 / 1.37
    # Bar lines land where the music leans, so the one-beat pickup sits at
    # the end of a first bar: three beats later than in the exported file.
    assert placed(exact_midi, score, shift=3) >= 0.95


def test_played_midi_converts_to_readable_notation(with_click):
    result = convert(with_click, to="musicxml")
    assert result.confidence == "exact"
    assert result.interpretation["played_midi"]
    reread = load(result.output).stream
    assert not [n for n in reread.recurse().notesAndRests if n.duration.tuplets]
    summary = inspect(with_click, bars=None)
    assert summary["interpretation"]["key"] == "F# minor"
    assert [p["instrument"] for p in summary["parts"]] == ["Piano", "Piano"]
    assert summary["tempos"] == [{"bar": 1, "bpm": 96, "text": ""}]


def test_count_in_bars_are_dropped(tmp_path):
    # Two bars of silence (a count-in) before a played scale.
    notes = [(4 + i * 0.5 + 0.01 * (-1) ** i, 4.4 + i * 0.5, 60 + i) for i in range(8)]
    score = load(write_midi(tmp_path / "count-in.mid", notes, program=73))
    first = score.stream.parts[0].flatten().notes[0]
    assert first.offset == 0


def test_triplets_are_found_beat_by_beat(tmp_path):
    third = 0.5 / 3
    notes = []
    for beat in range(8):
        if beat % 2:  # triplet eighths
            notes += [
                (beat * 0.5 + k * third + 0.004, beat * 0.5 + (k + 1) * third, 64)
                for k in range(3)
            ]
        else:  # straight eighths
            notes += [
                (beat * 0.5 + k * 0.25 - 0.004, beat * 0.5 + (k + 1) * 0.25, 60)
                for k in range(2)
            ]
    score = load(write_midi(tmp_path / "triplets.mid", notes, program=73))
    assert score.interpretation["grid"] == "16th notes and triplets"
    onsets = sorted(o for o, p in starts(score.stream) if p == 64)
    assert onsets[:3] == pytest.approx([1.0, 1 + 1 / 3, 1 + 2 / 3])


def test_single_line_instruments_get_one_staff_and_a_fitting_clef(tmp_path):
    notes = [
        (i * 0.5 + 0.012 * (-1) ** i, i * 0.5 + 0.4, 40 + i % 5) for i in range(16)
    ]
    score = load(write_midi(tmp_path / "bass.mid", notes, program=33))
    assert score.interpretation["staves"] == ["Electric Bass (finger)"]
    clefs = list(score.stream.recurse().getElementsByClass("Clef"))
    assert clefs[0].sign == "F"


def test_key_signature_in_the_file_wins(tmp_path):
    notes = [(i * 0.5 + 0.01 * (-1) ** i, i * 0.5 + 0.45, 61) for i in range(8)]
    major = load(write_midi(tmp_path / "db.mid", notes, key=1))
    assert major.interpretation["key"] == "C# major"
    minor = load(write_midi(tmp_path / "bbm.mid", notes, key=22))
    assert minor.interpretation["key"] == "Bb minor"
    names = {n.pitch.name for n in minor.stream.recurse().notes}
    assert names == {"D-"}


def test_drums_are_left_out_and_said_so(tmp_path):
    notes = [(i * 0.5 + 0.01 * (-1) ** i, i * 0.5 + 0.4, 67) for i in range(8)]
    path = write_midi(tmp_path / "band.mid", notes, drums=[0, 1, 2])
    assert "3 drum notes" in load(path).interpretation["left_out"]


def test_midi_without_pitched_notes(tmp_path):
    path = write_midi(tmp_path / "drums.mid", [], drums=[0.01, 1.02])
    with pytest.raises(ScoreError, match="no pitched notes"):
        performance.interpret(path)


def test_compound_meter_without_a_click(tmp_path):
    # A 6/8 jig at dotted quarter = 100, read with no click.
    from music21 import converter

    jig = converter.parse(
        "tinyNotation: 6/8 " + "c'4 d'8 e'4 f'8 g'4. e'4. " * 4 + "c'2."
    )
    exact = tmp_path / "jig.mid"
    jig.insert(0, __import__("music21").tempo.MetronomeMark(number=150))
    jig.write("midi", fp=str(exact))
    played = perform(exact, tmp_path / "jig-played.mid", 1.2, 0.02, 120, seed=4)
    score = load(played)
    assert score.interpretation["beat"] == "tracked (no click)"
    onsets = sorted({o for o, _ in starts(score.stream)})
    # Eighth-note grid in 6/8: every onset on a multiple of half a quarter.
    assert all(o * 2 == int(o * 2) for o in onsets)


def note(start, end, pitch=60, track=0):
    return performance.PlayedNote(
        Fraction(start), Fraction(end), pitch, 80, track, float(start)
    )


def test_a_key_struck_again_ends_the_earlier_note():
    notes = [note(0, 2), note(1, 2)]
    performance._tidy_lengths(notes)
    assert notes[0].end == 1


def test_legato_overlaps_are_trimmed_and_short_gaps_filled():
    legato = [note(0, Fraction(9, 8), 60), note(1, 2, 62)]
    performance._tidy_lengths(legato)
    assert legato[0].end == 1
    early = [note(0, Fraction(3, 4), 60), note(1, 2, 62)]
    performance._tidy_lengths(early)
    assert early[0].end == 1


def test_held_notes_under_moving_voices_are_kept():
    held = [note(0, 2, 55), note(0, Fraction(1, 2), 64), note(Fraction(1, 2), 1, 65)]
    performance._tidy_lengths(held)
    assert held[0].end == 2


def test_written_rests_survive():
    staccato = [note(0, Fraction(1, 2), 60), note(1, 2, 62)]
    performance._tidy_lengths(staccato)
    assert staccato[0].end == Fraction(1, 2)


def test_wide_chords_split_between_the_hands():
    chord = [note(0, 1, 36), note(0, 1, 43), note(0, 1, 72), note(0, 1, 76)]
    right, left = performance._hands(chord)
    assert [n.pitch for n in left] == [36, 43]
    assert [n.pitch for n in right] == [72, 76]


def test_left_hand_chords_reaching_above_middle_c_stay_left():
    # Oom-pah: a bass note, then a chord up to Eb4 under the melody.
    notes = [
        note(0, 1, 79),
        note(0, 1, 40),
        note(1, 2, 56),
        note(1, 2, 60),
        note(1, 2, 63),
        note(1, 2, 77),
    ]
    right, left = performance._hands(notes)
    assert {n.pitch for n in left} == {40, 56, 60, 63}
    assert {n.pitch for n in right} == {77, 79}


def test_long_notes_tie_across_moving_notes():
    from music21 import key

    notes = [note(0, 3, 48), note(0, 1, 64), note(1, 2, 65), note(2, 3, 67)]
    elements = performance._segments(notes, key.Key("C"), Fraction(4))
    held = [
        e.getTie(next(p for p in e.pitches if p.midi == 48)).type for e in elements[:3]
    ]
    assert held == ["start", "continue", "stop"]
    assert elements[-1].isRest


def test_spelling_follows_the_key():
    from music21 import key

    assert performance._spell(68, key.Key("A")).name == "G#"  # in the key
    assert performance._spell(68, key.Key("E-")).name == "A-"
    assert performance._spell(56, key.Key("a")).nameWithOctave == "G#3"  # raised 7th
    assert performance._spell(66, key.Key("B-")).name == "G-"  # flat key
    assert performance._spell(63, key.Key("D")).name == "D#"  # sharp key
    assert performance._spell(70, key.Key("C")).name == "B-"


def test_meter_defaults_to_four_four():
    assert performance._meter(pretty_midi.PrettyMIDI()) == (4, (4, 4))
