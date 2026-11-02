"""Choosing input and output paths without ever clobbering a user's file."""

from pathlib import Path

from scoreproof.errors import ScoreError


def input_path(path) -> Path:
    path = Path(path).expanduser()
    if not path.exists():
        raise ScoreError(f"File not found: {path}")
    return path.resolve()


def output_path(source: Path, extension: str, output=None, suffix="") -> Path:
    """Where to write a result made from ``source``.

    An explicit ``output`` file is used as given. Otherwise the result goes
    next to the source (or into ``output`` when it is a folder) under a name
    that does not exist yet, so nothing is overwritten.
    """
    name = f"{source.stem}{suffix}{extension}"
    if output is not None:
        output = Path(output).expanduser()
        if not output.is_dir():
            return output
        return _unused(output / name)
    return _unused(source.with_name(name))


def _unused(path: Path) -> Path:
    candidate, n = path, 2
    while candidate.exists():
        candidate = path.with_name(f"{path.stem}-{n}{path.suffix}")
        n += 1
    return candidate


def parse_bars(spec, total: int) -> tuple:
    """Turn "5", "1-8", "9-" or "-4" into an inclusive (start, end) bar range."""
    text = str(spec).replace(" ", "")
    try:
        if "-" in text:
            first, last = text.split("-", 1)
            start = int(first) if first else 1
            end = int(last) if last else total
        else:
            start = end = int(text)
    except ValueError:
        raise ScoreError(f"Bars must look like 5, 1-8 or 9-, not {spec!r}") from None
    if start > end:
        raise ScoreError(f"Bar range {spec!r} ends before it starts")
    return start, end
