"""Shared fixtures: real and synthetic score files in a temporary folder."""

import shutil

import pytest
from score_xml import attributes, note, score


@pytest.fixture
def chorale_file(tmp_path):
    """Bach's four-part chorale BWV 66.6 (BSD-licensed, bundled with music21)."""
    import music21

    path = tmp_path / "chorale.mxl"
    shutil.copy(music21.corpus.getWork("bach/bwv66.6"), path)
    return path


@pytest.fixture
def melody_file(tmp_path):
    """Four bars of C major on one flute part, with a tempo and a title."""
    extra = (
        "<key><fifths>0</fifths><mode>major</mode></key>"
        "<clef><sign>G</sign><line>2</line></clef>"
    )
    tempo = (
        '<direction placement="above"><direction-type><words>Allegro</words>'
        '</direction-type><sound tempo="120"/></direction>'
    )
    bars = [
        attributes(extra=extra) + tempo + note("C"),
        note("D"),
        note("E"),
        note("G"),
    ]
    xml = score(
        bars,
        name="Flute",
        score_part_extra="<score-instrument id='P1-I1'>"
        "<instrument-name>Flute</instrument-name></score-instrument>",
    ).replace(
        "<identification>",
        "<work><work-title>Little Tune</work-title></work><identification>",
    )
    path = tmp_path / "melody.musicxml"
    path.write_text(xml)
    return path
