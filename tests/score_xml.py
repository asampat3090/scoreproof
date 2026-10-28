"""Builders for tiny MusicXML documents that reproduce exporter quirks."""

ATTRIBUTES = (
    "<attributes><divisions>{divisions}</divisions>"
    "<time><beats>{beats}</beats><beat-type>4</beat-type></time>{extra}</attributes>"
)


def attributes(divisions=1, beats=4, extra=""):
    return ATTRIBUTES.format(divisions=divisions, beats=beats, extra=extra)


def note(step="C", octave=4, duration=4, kind="whole", inner="", before=""):
    return (
        f"<note>{before}<pitch><step>{step}</step><octave>{octave}</octave></pitch>"
        f"<duration>{duration}</duration><voice>1</voice><type>{kind}</type>"
        f"{inner}</note>"
    )


def score(measures, name="Piano", score_part_extra="", root="score-partwise"):
    body = "".join(
        f'<measure number="{i + 1}">{m}</measure>' for i, m in enumerate(measures)
    )
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        f'<{root} version="4.0"><identification><encoding>'
        "<software>Test Exporter 1.0</software></encoding></identification>"
        f'<part-list><score-part id="P1"><part-name>{name}</part-name>'
        f"{score_part_extra}</score-part></part-list>"
        f'<part id="P1">{body}</part></{root}>'
    )
