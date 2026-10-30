"""Convert a score file to another format and report exactly what survived."""

from dataclasses import asdict, dataclass, field
from pathlib import Path

from scoreproof import fidelity
from scoreproof.formats import detect_format, get_format
from scoreproof.io import load


@dataclass
class ConversionResult:
    output: Path
    source_format: str
    target_format: str
    confidence: str
    note_recall: float
    note_precision: float
    features_kept: dict
    expected_losses: list
    fixes: list = field(default_factory=list)
    irregular_bars: list = field(default_factory=list)
    warnings: list = field(default_factory=list)
    source: dict = field(default_factory=dict)
    result: dict = field(default_factory=dict)
    software: str = ""

    def to_dict(self) -> dict:
        data = asdict(self)
        data["output"] = str(self.output)
        return data


def convert(source, destination, to=None, source_format=None) -> ConversionResult:
    """Convert ``source`` to ``destination`` and verify the output.

    The output is read back and compared with the source note by note, so the
    result says what was kept, not just that a file was written.
    """
    target = get_format(to) if to else detect_format(destination)
    destination = Path(destination)
    if destination.suffix.lower() not in target.extensions:
        destination = destination.with_suffix(target.extension)
    score = load(source, source_format)
    written = score.save(destination, target.name)
    reread = load(written, target.name)
    report = fidelity.compare(score.stream, reread.stream, target.performed)
    return ConversionResult(
        output=written,
        source_format=score.format.name,
        target_format=target.name,
        confidence=report["confidence"],
        note_recall=report["note_recall"],
        note_precision=report["note_precision"],
        features_kept=report["features_kept"],
        expected_losses=list(target.cannot_hold),
        fixes=[asdict(f) for f in score.fixes],
        irregular_bars=score.irregular_bars,
        warnings=score.warnings,
        source=report["source"],
        result=report["output"],
        software=score.software,
    )
