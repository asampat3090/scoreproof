"""Convert a score file to another format and report exactly what survived."""

from dataclasses import asdict, dataclass, field
from pathlib import Path

from scoreproof import fidelity
from scoreproof.errors import ScoreError
from scoreproof.formats import FORMATS, detect_format, get_format
from scoreproof.io import load
from scoreproof.paths import input_path, output_path


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
    interpretation: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        data = asdict(self)
        data["output"] = str(self.output)
        return data


def convert(source, destination=None, to=None, source_format=None) -> ConversionResult:
    """Convert ``source`` to ``destination`` and verify the output.

    ``destination`` may be a file, a folder or omitted (next to the source,
    never overwriting an existing file); then ``to`` names the format. The
    output is read back and compared with the source note by note, so the
    result says what was kept, not just that a file was written.
    """
    if destination is None and to is None:
        raise ScoreError("Say which format to convert to, or give an output path")
    source = input_path(source)
    target = get_format(to) if to else detect_format(destination)
    destination = output_path(source, target.extension, destination)
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
        interpretation=score.interpretation,
    )


def convert_any(source, destination=None, to=None) -> dict:
    """Convert to a score format (verified note by note), or to sheet music
    (PDF, PNG, SVG) or audio (WAV), choosing by ``to`` or the output name."""
    from scoreproof.render import RENDER_FORMATS, render

    target = (to or (Path(destination).suffix if destination else "") or "").lower()
    target = target.lstrip(".")
    if target in RENDER_FORMATS:
        return render(source, target, destination)
    return convert(source, destination, to=to).to_dict()


def batch_convert(inputs, to, output_dir=None, on_progress=None) -> dict:
    """Convert many files (or every score in the given folders) to ``to``,
    a score format or pdf/png/svg/wav.

    One failure does not stop the batch; each file gets its own row.
    """
    from scoreproof.render import RENDER_FORMATS, render

    sheet = to.lower().lstrip(".") in RENDER_FORMATS
    target_name = to.lower().lstrip(".") if sheet else get_format(to).name
    files = score_files(inputs)
    if output_dir is not None:
        Path(output_dir).expanduser().mkdir(parents=True, exist_ok=True)
    rows = []
    for done, path in enumerate(files):
        row = {"input": str(path)}
        try:
            if sheet:
                row["output"] = render(path, target_name, output_dir)["output"]
            else:
                result = convert(path, output_dir, to=target_name)
                row.update(
                    output=str(result.output),
                    confidence=result.confidence,
                    note_recall=result.note_recall,
                )
        except ScoreError as exc:
            row["error"] = str(exc)
        rows.append(row)
        if on_progress:
            on_progress(done + 1, len(files))
    return {
        "target_format": target_name,
        "converted": sum("output" in r for r in rows),
        "failed": sum("error" in r for r in rows),
        "files": rows,
    }


def score_files(inputs) -> list:
    """Expand folders into the score files inside them (recursively)."""
    known = {ext for f in FORMATS.values() for ext in f.extensions}
    files = []
    for item in [inputs] if isinstance(inputs, (str, Path)) else inputs:
        path = input_path(item)
        if path.is_dir():
            files += sorted(
                p for p in path.rglob("*") if p.suffix.lower() in known and p.is_file()
            )
        else:
            files.append(path)
    if not files:
        raise ScoreError("No score files found")
    return files
