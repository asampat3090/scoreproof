"""Read MusicXML bytes and repair the exporter quirks that make parsers misread them.

Each repair rule targets a failure measured on real exports from notation apps
(see docs/converter-mcp-plan.md). Rules only rewrite what is broken and report
how many places they changed, so a conversion can say exactly what was fixed.
"""

import io
import re
import xml.etree.ElementTree as ET
import zipfile
from dataclasses import dataclass
from fractions import Fraction

from scoreproof.errors import ScoreError

MAX_DOCUMENT_BYTES = 200 * 1024 * 1024
_XML_DECLARATION = re.compile(r"^\s*<\?xml[^>]*\?>")
DEFAULT_PART_NAMES = {"", "musicxml part"}


@dataclass
class Fix:
    rule: str
    count: int
    description: str


@dataclass
class NormalizedMusicXML:
    xml: bytes
    fixes: list
    software: str
    irregular_bars: list


def read_document(data: bytes) -> bytes:
    """Return the MusicXML document, unzipping compressed .mxl containers."""
    if data[:4] != b"PK\x03\x04":
        return data
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        name = _rootfile(archive)
        if archive.getinfo(name).file_size > MAX_DOCUMENT_BYTES:
            raise ScoreError(f"{name} is too large to read")
        return archive.read(name)


def _rootfile(archive: zipfile.ZipFile) -> str:
    names = archive.namelist()
    if "META-INF/container.xml" in names:
        container = ET.fromstring(archive.read("META-INF/container.xml"))
        rootfile = container.find(".//rootfile")
        if rootfile is not None and rootfile.get("full-path") in names:
            return rootfile.get("full-path")
    for name in names:
        if not name.startswith("META-INF") and name.lower().endswith(
            (".xml", ".musicxml")
        ):
            return name
    raise ScoreError("No MusicXML document found inside the .mxl file")


def normalize(data: bytes) -> NormalizedMusicXML:
    """Parse MusicXML (or .mxl) bytes and apply every repair rule."""
    document = read_document(data)
    fixes = []
    if document[:2] in (b"\xff\xfe", b"\xfe\xff"):
        # Trust the byte-order mark over the declaration, which is sometimes
        # wrong; parsing text needs the declaration removed.
        document = _XML_DECLARATION.sub("", document.decode("utf-16"), count=1)
        fixes.append(Fix("utf16", 1, "Re-encoded a UTF-16 document as UTF-8"))
    try:
        root = ET.fromstring(document)
    except ET.ParseError as exc:
        raise ScoreError(f"Not valid XML: {exc}") from exc
    if root.tag == "score-partwise":
        for rule in _RULES:
            count = rule(root)
            if count:
                description = rule.__doc__.strip().splitlines()[0]
                fixes.append(Fix(rule.__name__, count, description))
    software = " | ".join(s.text.strip() for s in root.iter("software") if s.text)
    return NormalizedMusicXML(
        xml=ET.tostring(root, encoding="utf-8", xml_declaration=True),
        fixes=fixes,
        software=software,
        irregular_bars=find_irregular_bars(root),
    )


# ---------------------------------------------------------------- helpers ---


def _is_chord_member(note) -> bool:
    return note.find("chord") is not None


def _time_length(attributes, current):
    """Bar length in quarter notes from a <time> element, or ``current``."""
    time = attributes.find("time")
    if time is None:
        return current
    if time.find("senza-misura") is not None:
        return None
    total = Fraction(0)
    for beats, beat_type in zip(time.findall("beats"), time.findall("beat-type")):
        try:
            count = sum(Fraction(b) for b in beats.text.split("+"))
            total += count * 4 / Fraction(beat_type.text)
        except (ValueError, ZeroDivisionError, AttributeError):
            return None
    return total or None


def _part_measures(part):
    """Yield (measure, divisions, bar_length), tracking <attributes> changes."""
    divisions, bar_length = None, Fraction(4)
    for measure in part.findall("measure"):
        for attributes in measure.findall("attributes"):
            text = attributes.findtext("divisions")
            if text and text.strip():
                divisions = Fraction(text.strip())
            bar_length = _time_length(attributes, bar_length)
        yield measure, divisions, bar_length


def _measures(root):
    for part in root.findall("part"):
        for measure, divisions, bar_length in _part_measures(part):
            yield part, measure, divisions, bar_length


def _duration(element, divisions) -> Fraction:
    text = element.findtext("duration")
    if not text or not divisions:
        return Fraction(0)
    return Fraction(text.strip()) / divisions


def _measure_extent(measure, divisions) -> Fraction:
    """The furthest point any voice reaches in a measure, in quarter notes."""
    position = furthest = last = Fraction(0)
    for element in measure:
        if element.tag == "note":
            if element.find("grace") is not None:
                continue
            length = _duration(element, divisions)
            if _is_chord_member(element):
                position += length - last
            else:
                position += length
                last = length
        elif element.tag == "backup":
            position = max(Fraction(0), position - _duration(element, divisions))
        elif element.tag == "forward":
            position += _duration(element, divisions)
        furthest = max(furthest, position)
    return furthest


def _rebuild(measure, ordered):
    measure[:] = ordered


# ------------------------------------------------------------------ rules ---


def fill_note_free_bars(root) -> int:
    """Give bars with no notes an explicit whole-bar rest.

    Dorico writes empty bars as a bare <forward>. Combined with mid-bar
    tempo changes (a written ritardando), music21 then shortens the bar
    and every later bar drifts out of time.
    """
    count = 0
    for _, measure, divisions, bar_length in _measures(root):
        if measure.find("note") is not None or not divisions:
            continue
        length = _measure_extent(measure, divisions)
        if not length:
            if measure.get("implicit") == "yes" or not bar_length:
                continue
            length = bar_length
        rest = ET.Element("note")
        ET.SubElement(rest, "rest", measure="yes")
        ET.SubElement(rest, "duration").text = str(length * divisions)
        ET.SubElement(rest, "voice").text = "1"
        kept = [e for e in measure if e.tag not in ("forward", "backup")]
        right = [i for i, e in enumerate(kept) if e.tag == "barline" and _is_right(e)]
        kept.insert(right[0] if right else len(kept), rest)
        _rebuild(measure, kept)
        count += 1
    return count


def _is_right(barline) -> bool:
    return barline.get("location", "right") == "right"


def move_directions_out_of_chords(root) -> int:
    """Move <direction> elements that sit between the notes of one chord.

    Guitar Pro does this; music21 then plays the chord notes one after
    another instead of together, stretching the bar.
    """
    count = 0
    for _, measure, _, _ in _measures(root):
        ordered, root_index, pending, moved = [], None, [], 0
        for element in measure:
            if element.tag == "note" and _is_chord_member(element):
                if pending and root_index is not None:
                    for d in pending:
                        ordered.remove(d)
                    ordered[root_index:root_index] = pending
                    root_index += len(pending)
                    moved += len(pending)
                pending = []
            elif element.tag == "note":
                root_index, pending = len(ordered), []
            elif element.tag == "direction":
                pending.append(element)
            else:
                root_index, pending = None, []
            ordered.append(element)
        if moved:
            _rebuild(measure, ordered)
            count += moved
    return count


def hoist_note_dynamics(root) -> int:
    """Turn dynamics attached to a note into a direction before the note.

    Notion (and some Guitar Pro exports) put dynamics inside <notations>,
    which music21 ignores, so every such marking is silently dropped.
    """
    count = 0
    for _, measure, _, _ in _measures(root):
        ordered, root_index, changed = [], 0, 0
        for element in measure:
            if element.tag == "note" and not _is_chord_member(element):
                root_index = len(ordered)
            if element.tag == "note":
                for direction in _take_dynamics(element):
                    ordered.insert(root_index, direction)
                    root_index += 1
                    changed += 1
            ordered.append(element)
        if changed:
            _rebuild(measure, ordered)
            count += changed
    return count


def _take_dynamics(note):
    directions = []
    for notations in note.findall("notations"):
        for dynamics in notations.findall("dynamics"):
            notations.remove(dynamics)
            direction = ET.Element(
                "direction", placement=dynamics.get("placement", "below")
            )
            ET.SubElement(direction, "direction-type").append(dynamics)
            for tag in ("voice", "staff"):
                value = note.findtext(tag)
                if value:
                    ET.SubElement(direction, tag).text = value
            directions.append(direction)
        if len(notations) == 0:
            note.remove(notations)
    return directions


def complete_transposes(root) -> int:
    """Add the missing steps to octave-changing <transpose> elements.

    Flat writes octave-only transposes for instruments such as guitar.
    music21 needs both <diatonic> and <chromatic> next to <octave-change>
    and otherwise crashes with a TypeError.
    """
    count = 0
    for transpose in root.iter("transpose"):
        if transpose.find("octave-change") is None:
            continue
        chromatic = transpose.find("chromatic")
        if chromatic is None:
            chromatic = ET.Element("chromatic")
            chromatic.text = "0"
            index = 1 if transpose.find("diatonic") is not None else 0
            transpose.insert(index, chromatic)
            count += 1
        if transpose.find("diatonic") is None:
            diatonic = ET.Element("diatonic")
            diatonic.text = str(_diatonic_steps(int(chromatic.text.strip())))
            transpose.insert(0, diatonic)
            count += 1
    return count


# Diatonic steps for a transposition of 0-11 semitones, using the usual
# spelling of each interval (minor 2nd, major 2nd, minor 3rd, ...).
_DIATONIC_FOR_SEMITONES = (0, 1, 1, 2, 2, 3, 3, 4, 5, 5, 6, 6)


def _diatonic_steps(semitones: int) -> int:
    octaves, rest = divmod(abs(semitones), 12)
    steps = octaves * 7 + _DIATONIC_FOR_SEMITONES[rest]
    return -steps if semitones < 0 else steps


def repair_dangling_ties(root) -> int:
    """Remove tie starts that never reach a matching tie stop.

    Sibelius leaves these in about a quarter of its exports. Left in place
    they can merge unrelated notes on conversion to MIDI.
    """
    count = 0
    for part in root.findall("part"):
        open_ties = {}
        for note in part.iter("note"):
            key = _pitch_key(note)
            if key is None:
                continue
            types = {t.get("type") for t in note.findall("tie")}
            if "stop" not in types and key in open_ties:
                count += _drop_tie_start(open_ties.pop(key))
            if "stop" in types:
                open_ties.pop(key, None)
            if "start" in types:
                open_ties[key] = note
        for note in open_ties.values():
            count += _drop_tie_start(note)
    return count


def _pitch_key(note):
    pitch = note.find("pitch")
    if pitch is None:
        return None
    return (
        pitch.findtext("step"),
        pitch.findtext("alter") or "0",
        pitch.findtext("octave"),
        note.findtext("staff") or "1",
    )


def _drop_tie_start(note) -> int:
    for tie in note.findall("tie"):
        if tie.get("type") == "start":
            note.remove(tie)
    for notations in note.findall("notations"):
        for tied in notations.findall("tied"):
            if tied.get("type") == "start":
                notations.remove(tied)
        if len(notations) == 0:
            note.remove(notations)
    return 1


def name_parts(root) -> int:
    """Replace empty or placeholder part names ("MusicXML Part")."""
    count = 0
    for number, score_part in enumerate(root.iter("score-part"), start=1):
        name = score_part.find("part-name")
        if name is None:
            name = ET.SubElement(score_part, "part-name")
        if (name.text or "").strip().lower() not in DEFAULT_PART_NAMES:
            continue
        instrument = score_part.findtext("score-instrument/instrument-name") or ""
        if instrument.strip().lower() in DEFAULT_PART_NAMES:
            instrument = f"Part {number}"
        name.text = instrument.strip()
        count += 1
    return count


# Accidental and notehead values music21 10 accepts; others make it raise.
_SUPPORTED_ACCIDENTALS = {
    "natural",
    "sharp",
    "double-sharp",
    "sharp-sharp",
    "triple-sharp",
    "quadruple-sharp",
    "flat",
    "double-flat",
    "flat-flat",
    "triple-flat",
    "quadruple-flat",
    "half-sharp",
    "quarter-sharp",
    "one-and-a-half-sharp",
    "three-quarters-sharp",
    "half-flat",
    "quarter-flat",
    "one-and-a-half-flat",
    "three-quarters-flat",
}
_ACCIDENTAL_SUBSTITUTES = {"natural-flat": "flat", "natural-sharp": "sharp"}
_SUPPORTED_NOTEHEADS = {
    "arrow down",
    "arrow up",
    "back slashed",
    "circle dot",
    "circle-x",
    "circled",
    "cluster",
    "cross",
    "diamond",
    "do",
    "fa",
    "fa up",
    "inverted triangle",
    "la",
    "left triangle",
    "mi",
    "none",
    "normal",
    "other",
    "re",
    "rectangle",
    "slash",
    "slashed",
    "so",
    "square",
    "ti",
    "triangle",
    "x",
}
_STEPS = set("ABCDEFG")


def drop_unsupported_accidentals(root) -> int:
    """Replace or remove accidental symbols music21 cannot read.

    Microtonal (Sagittal, Persian) and combined accidentals such as
    natural-flat crash music21. The <alter> value still sets the pitch, so
    at most the printed symbol is lost.
    """
    count = 0
    for parent in root.iter():
        for element in list(parent):
            if element.tag not in ("accidental", "key-accidental"):
                continue
            value = (element.text or "").strip().lower()
            if value in _SUPPORTED_ACCIDENTALS:
                continue
            if value in _ACCIDENTAL_SUBSTITUTES:
                element.text = _ACCIDENTAL_SUBSTITUTES[value]
            else:
                parent.remove(element)
            count += 1
    return count


def normalize_noteheads(root) -> int:
    """Map notehead shapes music21 does not know to "other".

    Noteflight writes "named" noteheads (SMuFL glyph names), which crash
    music21.
    """
    count = 0
    for notehead in root.iter("notehead"):
        if (notehead.text or "").strip().lower() not in _SUPPORTED_NOTEHEADS:
            notehead.text = "other"
            count += 1
    return count


def simplify_time_signatures(root) -> int:
    """Rewrite time signatures music21 cannot represent as exact equivalents.

    Fractional beats (3.5/8) become 7/16 and very short bars (4/256) become
    1/64, so every bar keeps its exact length.
    """
    count = 0
    for time in root.iter("time"):
        for beats, beat_type in zip(time.findall("beats"), time.findall("beat-type")):
            rewritten = _simpler_meter(beats.text, beat_type.text)
            if rewritten:
                beats.text, beat_type.text = rewritten
                count += 1
    return count


def _simpler_meter(beats: str, beat_type: str):
    try:
        if "+" in beats:
            return None
        count, unit = Fraction(beats.strip()), int(beat_type.strip())
    except (ValueError, AttributeError):
        return None
    if count.denominator == 1 and unit <= 64 and unit & (unit - 1) == 0:
        return None
    value = count / unit
    for denominator in (1, 2, 4, 8, 16, 32, 64, 128, 256):
        if (value * denominator).denominator == 1:
            return str(value * denominator), str(denominator)
    return None


def convert_function_harmonies(root) -> int:
    """Turn chord symbols without a root note into plain text.

    music21 only reads chord symbols that name a root. MuseScore 4 exports
    Roman-numeral and Nashville-number symbols, which crash it; as text they
    still print.
    """
    count = 0
    for _, measure, _, _ in _measures(root):
        for index, element in enumerate(list(measure)):
            if element.tag != "harmony":
                continue
            step = (element.findtext("root/root-step") or "").strip().upper()
            if step in _STEPS:
                continue
            direction = ET.Element(
                "direction", placement=element.get("placement", "above")
            )
            words = ET.SubElement(ET.SubElement(direction, "direction-type"), "words")
            words.text = _harmony_text(element)
            measure.remove(element)
            measure.insert(index, direction)
            count += 1
    return count


def _harmony_text(harmony) -> str:
    kind = harmony.find("kind")
    suffix = kind.get("text", "") if kind is not None else ""
    label = (
        harmony.findtext("function")
        or harmony.findtext("numeral/numeral-root")
        or harmony.findtext("root/root-step")
        or ""
    )
    return (label.strip() + suffix).strip() or "?"


def find_irregular_bars(root) -> list:
    """List bars whose contents do not fill the time signature.

    These are reported, not changed: some are deliberate (cadenzas), and
    guessing a repair would be worse than flagging it.
    """
    found = []
    for part in root.findall("part"):
        measures = part.findall("measure")
        edges = {id(m) for m in measures[:1] + measures[-1:]}
        for measure, divisions, bar_length in _part_measures(part):
            if id(measure) in edges:
                continue
            if not divisions or not bar_length or measure.get("implicit") == "yes":
                continue
            extent = _measure_extent(measure, divisions)
            if extent != bar_length:
                found.append(
                    {
                        "part": part.get("id"),
                        "measure": measure.get("number"),
                        "length": float(extent),
                        "expected": float(bar_length),
                    }
                )
    return found


_RULES = (
    complete_transposes,
    simplify_time_signatures,
    drop_unsupported_accidentals,
    normalize_noteheads,
    convert_function_harmonies,
    name_parts,
    move_directions_out_of_chords,
    hoist_note_dynamics,
    fill_note_free_bars,
    repair_dangling_ties,
)
