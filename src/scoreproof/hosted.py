"""Scoreproof on the web: an MCP connector and a drop page, for Cloud Run.

Remote AI clients (claude.ai, ChatGPT) cannot see files on anyone's computer,
so the hosted tools take a link to a file (from the drop page or any public
https URL) or the score itself as text, and return download links. Files are
kept for a day. Run it with ``scoreproof serve``.

Settings come from the environment:
- ``SCOREPROOF_PUBLIC_URL``: the address people reach the service at.
- ``SCOREPROOF_BUCKET``: a Cloud Storage bucket for files (with a lifecycle
  rule that deletes them after a day); without it files go in a local folder,
  ``SCOREPROOF_FILES`` (default: a folder in the system temp directory).
"""

import collections
import functools
import ipaddress
import json
import mimetypes
import os
import re
import secrets
import socket
import tempfile
import time
import urllib.parse
import urllib.request
from importlib import resources
from pathlib import Path
from typing import Annotated, Any, Literal

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.server.transport_security import TransportSecuritySettings
from mcp_types import ToolAnnotations
from pydantic import Field
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse, Response
from starlette.routing import Route

from scoreproof import __version__, analysis, formats, transform
from scoreproof.convert import convert_any
from scoreproof.errors import ScoreError
from scoreproof.render import render as render_score

MAX_BYTES = 10 * 1024 * 1024
KEEP_SECONDS = 24 * 60 * 60
FETCH_TIMEOUT = 15
# Requests allowed per client IP in a sliding window, by path. One tool call
# over MCP is a few requests (initialize, list tools, call).
RATE_LIMITS = {"/mcp": (300, 600), "/upload": (30, 600), "/api/convert": (30, 600)}
# Rendered pages are safe to show in the browser; everything else downloads,
# so an uploaded XML file can never run as a page on this site.
SHOW_INLINE = {".pdf", ".png", ".svg", ".wav"}

INSTRUCTIONS = """\
Scoreproof converts, checks and transforms music files: MusicXML, MIDI, ABC,
MEI and Humdrum. It makes printable sheet music (PDF, PNG) and audio.

- Give each tool either `file`, a link to the music file, or `content`, the
  score itself as text (ABC, MusicXML, MEI or Humdrum) with a `filename`.
- Files attached in this chat cannot be passed on. For MIDI or other files,
  ask the user to upload them at {upload_url} and paste the link it gives.
- To write music yourself, compose it in ABC and pass it as `content`, then
  render it to PDF so the user gets sheet music.
- Results are download links that work for a day. Share them with the user.
- Every conversion is verified note by note. Tell the user the confidence and
  anything that was lost.
- To read or discuss the music, use `inspect`: it returns a summary and an ABC
  excerpt.
- MIDI played in a DAW or on a keyboard is read as notation automatically.
  Results include an `interpretation`; mention the tempo and key it chose.
- Native Sibelius, Dorico, Finale and MuseScore files are not supported. Ask
  the user to export MusicXML from their app.
"""


def public_url() -> str:
    return os.environ.get("SCOREPROOF_PUBLIC_URL", "http://localhost:8080").rstrip("/")


# Storage --------------------------------------------------------------------


class LocalStore:
    """Files in a local folder; for running on one machine."""

    def __init__(self, root):
        self.root = Path(root)

    def put(self, key: str, data: bytes):
        path = self.root / key
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)

    def get(self, key: str) -> bytes | None:
        path = self.root / key
        if not path.is_file() or time.time() - path.stat().st_mtime > KEEP_SECONDS:
            return None
        return path.read_bytes()


class CloudStore:
    """Files in a Cloud Storage bucket, which deletes them after a day."""

    def __init__(self, bucket: str):
        from google.cloud import storage

        self.bucket = storage.Client().bucket(bucket)

    def put(self, key: str, data: bytes):
        self.bucket.blob(key).upload_from_string(data, content_type=_media_type(key))

    def get(self, key: str) -> bytes | None:
        from google.cloud.exceptions import NotFound

        try:
            return self.bucket.blob(key).download_as_bytes()
        except NotFound:
            return None


@functools.cache
def store():
    bucket = os.environ.get("SCOREPROOF_BUCKET")
    if bucket:
        return CloudStore(bucket)
    default = Path(tempfile.gettempdir()) / "scoreproof-files"
    return LocalStore(os.environ.get("SCOREPROOF_FILES", default))


def save(name: str, data: bytes) -> str:
    """Keep ``data`` for a day and return its download link."""
    key = f"{secrets.token_urlsafe(16)}/{_safe_name(name)}"
    store().put(key, data)
    return f"{public_url()}/files/{key}"


def _safe_name(name: str) -> str:
    name = re.sub(r"[^A-Za-z0-9._-]+", "-", Path(name).name).strip("-.")
    return name or "score"


def _media_type(name: str) -> str:
    return mimetypes.guess_type(name)[0] or "application/octet-stream"


# Getting the music in -------------------------------------------------------


def _music_name(name: str) -> str:
    """``name`` if it is a music file Scoreproof reads, else a helpful error."""
    name = _safe_name(name)
    try:
        formats.get_format(Path(name).suffix or name)
    except ScoreError:
        raise ScoreError(
            f"{name!r} is not a music file Scoreproof reads. Use MusicXML (.musicxml,"
            " .xml, .mxl), MIDI (.mid), ABC (.abc), MEI (.mei) or Humdrum (.krn)."
        ) from None
    return name


def read_link(link: str) -> tuple[str, bytes]:
    """The file name and bytes behind a link from the drop page or the web."""
    own = f"{public_url()}/files/"
    if link.startswith(own):
        key = urllib.parse.unquote(link[len(own) :])
        data = store().get(key)
        if data is None:
            raise ScoreError(
                "That link has expired or is wrong. Upload the file again."
            )
        return key.rsplit("/", 1)[-1], data
    return _fetch(link)


class _CheckedRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        _check_public(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _check_public(url: str):
    """Only fetch https URLs on the public internet, never this server's own
    network (the metadata server also needs a header these requests lack)."""
    parts = urllib.parse.urlsplit(url)
    if parts.scheme != "https" or not parts.hostname:
        raise ScoreError("Links must start with https://")
    try:
        addresses = socket.getaddrinfo(parts.hostname, parts.port or 443)
    except socket.gaierror:
        raise ScoreError(f"Could not find {parts.hostname}") from None
    for *_, address in addresses:
        if not ipaddress.ip_address(address[0]).is_global:
            raise ScoreError("Links must point to a public website")


def _fetch(url: str) -> tuple[str, bytes]:
    _check_public(url)
    opener = urllib.request.build_opener(_CheckedRedirects)
    request = urllib.request.Request(url, headers={"User-Agent": "scoreproof"})
    try:
        with opener.open(request, timeout=FETCH_TIMEOUT) as response:
            data = response.read(MAX_BYTES + 1)
            final = response.url
    except OSError as exc:
        raise ScoreError(f"Could not download {url}: {exc}") from None
    if len(data) > MAX_BYTES:
        raise ScoreError("Files must be 10 MB or smaller")
    name = urllib.parse.unquote(urllib.parse.urlsplit(final).path.rsplit("/", 1)[-1])
    return _music_name(name), data


def _bring_in(folder: Path, file=None, content=None, filename=None) -> Path:
    if (file is None) == (content is None):
        raise ScoreError(
            "Give either file (a link to the music) or content (the score as text)"
        )
    if content is not None:
        name = _music_name(filename or "tune.abc")
        data = content.encode("utf-8")
        if len(data) > MAX_BYTES:
            raise ScoreError("Files must be 10 MB or smaller")
    else:
        name, data = read_link(file)
    folder.mkdir()
    path = folder / name
    path.write_bytes(data)
    return path


# Running tools ---------------------------------------------------------------


def run_on(operation, *sources: dict) -> dict:
    """Bring each source into a scratch folder, run ``operation`` on the
    paths, and swap the files it made for download links."""
    with tempfile.TemporaryDirectory() as tmp:
        folder = Path(tmp).resolve()
        paths = [_bring_in(folder / str(n), **s) for n, s in enumerate(sources)]
        try:
            result = operation(*paths)
        except ScoreError as exc:
            raise ScoreError(str(exc).replace(f"{folder}/", "")) from None
        inputs = {str(p): p.name for p in paths}
        return _published(result, str(folder), inputs)


def _published(value, folder: str, inputs: dict):
    if isinstance(value, dict):
        return {k: _published(v, folder, inputs) for k, v in value.items()}
    if isinstance(value, list):
        return [_published(v, folder, inputs) for v in value]
    if isinstance(value, str) and value.startswith(folder):
        if value in inputs:
            return inputs[value]
        path = Path(value)
        if path.is_file():
            return save(path.name, path.read_bytes())
        return value.replace(f"{folder}/", "")
    return value


def _tool_errors(fn):
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except ScoreError as exc:
            raise ToolError(str(exc)) from exc

    return wrapper


# The MCP connector ------------------------------------------------------------

server = MCPServer(
    "scoreproof",
    title="Scoreproof",
    instructions=INSTRUCTIONS.format(upload_url=public_url()),
    version=__version__,
    website_url="https://github.com/asampat3090/scoreproof",
)

READ_ONLY = ToolAnnotations(readOnlyHint=True, openWorldHint=True)
MAKES_FILE = ToolAnnotations(
    readOnlyHint=False, destructiveHint=False, idempotentHint=False, openWorldHint=True
)
File = Annotated[
    str | None,
    Field(
        description="Link to the music file: from the Scoreproof upload page, "
        "or any public https URL"
    ),
]
Content = Annotated[
    str | None,
    Field(description="The score itself as text (ABC, MusicXML, MEI or Humdrum)"),
]
Filename = Annotated[
    str | None,
    Field(description="Name for content, with its extension (default tune.abc)"),
]
Parts = Annotated[
    list[str] | None,
    Field(description="Parts by number (1-based) or name; default all"),
]
Format = Annotated[str | None, Field(description="Output format (default: same)")]


@server.tool(name="convert", title="Convert a music file", annotations=MAKES_FILE)
@_tool_errors
def convert_file(
    to: Annotated[
        str,
        Field(
            description="musicxml, mxl, midi, abc, mei, humdrum, or pdf/png/svg "
            "(sheet music) or wav (audio)"
        ),
    ],
    file: File = None,
    content: Content = None,
    filename: Filename = None,
) -> dict[str, Any]:
    """Convert a score between formats and verify the result note by note,
    or turn it into sheet music (pdf, png, svg) or audio (wav).

    Returns a download link, a confidence level (exact, high, medium, low),
    the share of notes kept, features the target format cannot hold, and
    exporter quirks that were repaired on the way in.
    """
    source = {"file": file, "content": content, "filename": filename}
    return run_on(lambda path: convert_any(path, to=to), source)


@server.tool(title="Render sheet music or audio", annotations=MAKES_FILE)
@_tool_errors
def render(
    to: Annotated[
        Literal["pdf", "png", "svg", "wav"],
        Field(description="pdf: printable sheet music; png/svg: page images; wav"),
    ] = "pdf",
    page_size: Annotated[
        Literal["letter", "a4"], Field(description="Paper size for pdf and png")
    ] = "letter",
    file: File = None,
    content: Content = None,
    filename: Filename = None,
) -> dict[str, Any]:
    """Engrave sheet music to print or view (PDF, PNG, SVG) or make an audio
    preview (WAV). Returns download links."""
    source = {"file": file, "content": content, "filename": filename}
    return run_on(lambda path: render_score(path, to, page_size=page_size), source)


@server.tool(title="Look at a score", annotations=READ_ONLY)
@_tool_errors
def inspect(
    bars: Annotated[
        str | None,
        Field(description='Bars to show as ABC, e.g. "1-8" (max 32); "" for none'),
    ] = analysis.DEFAULT_EXCERPT,
    parts: Parts = None,
    file: File = None,
    content: Content = None,
    filename: Filename = None,
) -> dict[str, Any]:
    """Summarise a score and show some bars as ABC notation: title, parts with
    instruments and ranges, key, meters, tempos and bar count."""
    source = {"file": file, "content": content, "filename": filename}
    return run_on(lambda path: analysis.inspect(path, bars or None, parts), source)


@server.tool(title="Check a score for problems", annotations=READ_ONLY)
@_tool_errors
def validate(
    file: File = None, content: Content = None, filename: Filename = None
) -> dict[str, Any]:
    """Check a score: exporter quirks that needed repair, bars that do not
    fill their time signature, and notes outside an instrument's range."""
    source = {"file": file, "content": content, "filename": filename}
    return run_on(analysis.validate, source)


@server.tool(title="Compare two versions", annotations=READ_ONLY)
@_tool_errors
def compare(
    a: Annotated[str, Field(description="Link to the first version")],
    b: Annotated[str, Field(description="Link to the second version")],
) -> dict[str, Any]:
    """Show what changed between two versions of a piece, bar by bar, even
    across formats."""
    return run_on(analysis.compare, {"file": a}, {"file": b})


@server.tool(title="Transpose a score", annotations=MAKES_FILE)
@_tool_errors
def transpose(
    interval: Annotated[
        str | None,
        Field(description='Interval ("M2", "-m3", "P5") or semitones ("2", "-3")'),
    ] = None,
    key: Annotated[
        str | None, Field(description='Target key, e.g. "D", "Bb major", "F# minor"')
    ] = None,
    pitch: Annotated[
        Literal["concert", "written"] | None,
        Field(description="Switch transposing instruments to concert or written pitch"),
    ] = None,
    to: Format = None,
    file: File = None,
    content: Content = None,
    filename: Filename = None,
) -> dict[str, Any]:
    """Transpose a score by an interval, into a key, or between concert and
    written pitch. Give exactly one of interval, key or pitch."""
    source = {"file": file, "content": content, "filename": filename}
    return run_on(
        lambda path: transform.transpose(path, interval, key, pitch, None, to), source
    )


@server.tool(title="Extract parts or bars", annotations=MAKES_FILE)
@_tool_errors
def extract(
    parts: Parts = None,
    bars: Annotated[
        str | None, Field(description='Bar range such as "1-8", "5" or "9-"')
    ] = None,
    each_part: Annotated[
        bool, Field(description="One file per part (to make parts for players)")
    ] = False,
    to: Format = None,
    file: File = None,
    content: Content = None,
    filename: Filename = None,
) -> dict[str, Any]:
    """Save chosen parts and/or bars of a score as new files."""
    source = {"file": file, "content": content, "filename": filename}
    return run_on(
        lambda path: transform.extract(path, parts, bars, each_part, None, to), source
    )


@server.tool(title="List supported formats", annotations=READ_ONLY)
def list_formats() -> dict[str, Any]:
    """Formats Scoreproof reads and writes, and what each one cannot hold."""
    return formats.describe_formats()


# The web side -------------------------------------------------------------------


async def home(request: Request) -> HTMLResponse:
    page = resources.files("scoreproof").joinpath("drop.html").read_text()
    return HTMLResponse(page.replace("{{MCP_URL}}", f"{public_url()}/mcp"))


async def privacy(request: Request) -> HTMLResponse:
    return HTMLResponse(
        resources.files("scoreproof").joinpath("privacy.html").read_text()
    )


async def icon(request: Request) -> Response:
    data = resources.files("scoreproof").joinpath("icon.png").read_bytes()
    return Response(
        data, media_type="image/png", headers={"Cache-Control": "public, max-age=86400"}
    )


async def health(request: Request) -> JSONResponse:
    return JSONResponse({"ok": True, "version": __version__})


async def upload(request: Request) -> JSONResponse:
    """Keep an uploaded file for a day: ``POST /upload?name=song.mid``."""
    try:
        name = _music_name(request.query_params.get("name", ""))
    except ScoreError as exc:
        return _error(str(exc), 400)
    data = bytearray()
    async for chunk in request.stream():
        data += chunk
        if len(data) > MAX_BYTES:
            return _error("Files must be 10 MB or smaller", 413)
    if not data:
        return _error("The file is empty", 400)
    url = save(name, bytes(data))
    return JSONResponse({"url": url, "name": name, "expires_in_hours": 24})


async def convert_upload(request: Request) -> JSONResponse:
    """Convert a link for the drop page: ``{"file": link, "to": "pdf"}``."""
    from starlette.concurrency import run_in_threadpool

    try:
        body = await request.json()
        file, to = body["file"], body["to"]
    except (ValueError, KeyError, TypeError):
        return _error('Send JSON like {"file": "<link>", "to": "pdf"}', 400)
    try:
        result = await run_in_threadpool(
            run_on, lambda path: convert_any(path, to=to), {"file": file}
        )
    except ScoreError as exc:
        return _error(str(exc), 422)
    return JSONResponse(result)


async def download(request: Request) -> Response:
    key = request.path_params["key"]
    data = store().get(key)
    if data is None:
        return _error("This file has expired (files are kept for a day)", 404)
    name = key.rsplit("/", 1)[-1]
    shown = "inline" if Path(name).suffix.lower() in SHOW_INLINE else "attachment"
    return Response(
        data,
        media_type=_media_type(name),
        headers={
            "Content-Disposition": f'{shown}; filename="{name}"',
            "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'",
            "X-Content-Type-Options": "nosniff",
            "Cache-Control": "private, max-age=3600",
        },
    )


def _error(message: str, status: int) -> JSONResponse:
    return JSONResponse({"error": message}, status_code=status)


class RateLimit:
    """Per-IP sliding-window limits on POSTs (ASGI middleware).

    Limits are per instance; the instance cap bounds the total.
    """

    MAX_TRACKED = 50_000

    def __init__(self, app, limits=RATE_LIMITS):
        self.app = app
        self.limits = limits
        self.seen = collections.defaultdict(collections.deque)

    async def __call__(self, scope, receive, send):
        limit = self._limit(scope)
        if limit and not self._allow(_client_ip(scope), scope["path"], *limit):
            body = json.dumps(
                {"error": "Too many requests from your address. Try again shortly."}
            ).encode()
            await send(
                {
                    "type": "http.response.start",
                    "status": 429,
                    "headers": [
                        (b"content-type", b"application/json"),
                        (b"retry-after", b"60"),
                    ],
                }
            )
            await send({"type": "http.response.body", "body": body})
            return
        await self.app(scope, receive, send)

    def _limit(self, scope):
        if scope["type"] != "http" or scope["method"] != "POST":
            return None
        return self.limits.get(scope["path"].rstrip("/"))

    def _allow(self, ip, path, count, window) -> bool:
        now = time.monotonic()
        hits = self.seen[(ip, path.rstrip("/"))]
        while hits and now - hits[0] > window:
            hits.popleft()
        if len(hits) >= count:
            return False
        hits.append(now)
        if len(self.seen) > self.MAX_TRACKED:
            self._forget_idle(now)
        return True

    def _forget_idle(self, now):
        longest = max(window for _, window in self.limits.values())
        for key in [k for k, hits in self.seen.items() if now - hits[-1] > longest]:
            del self.seen[key]


def _client_ip(scope) -> str:
    """The caller's address. Cloud Run appends the real client address to
    X-Forwarded-For, so the last entry is the one a caller cannot forge."""
    for name, value in scope.get("headers", []):
        if name == b"x-forwarded-for":
            return value.decode().split(",")[-1].strip()
    client = scope.get("client")
    return client[0] if client else "unknown"


def create_app():
    app = server.streamable_http_app(
        stateless_http=True,
        json_response=True,
        transport_security=TransportSecuritySettings(
            enable_dns_rebinding_protection=False
        ),
        host="0.0.0.0",
    )
    app.router.routes.extend(
        [
            Route("/", home),
            Route("/health", health),
            Route("/privacy", privacy),
            Route("/favicon.ico", icon),
            Route("/icon.png", icon),
            Route("/upload", upload, methods=["POST"]),
            Route("/api/convert", convert_upload, methods=["POST"]),
            Route("/files/{key:path}", download),
        ]
    )
    return RateLimit(app)


def main(host="127.0.0.1", port=8080):
    import uvicorn

    uvicorn.run(
        create_app(), host=host, port=port, proxy_headers=True, forwarded_allow_ips="*"
    )
