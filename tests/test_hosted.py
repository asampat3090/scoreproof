"""Tests for the hosted service: storage, links, MCP tools and web routes."""

import os
import socket
import sys
import types
import urllib.request

import anyio
import pytest
from mcp.client import Client
from starlette.testclient import TestClient

from scoreproof import cli, hosted
from scoreproof.errors import ScoreError

SITE = "https://scoreproof.test"
TUNE = "X:1\nT:Little Waltz\nM:3/4\nL:1/4\nK:G\nG A B|c2 B|A G F|G3|]\n"


@pytest.fixture(autouse=True)
def service(tmp_path, monkeypatch):
    monkeypatch.setenv("SCOREPROOF_PUBLIC_URL", SITE)
    monkeypatch.setenv("SCOREPROOF_FILES", str(tmp_path / "files"))
    monkeypatch.delenv("SCOREPROOF_BUCKET", raising=False)
    hosted.store.cache_clear()
    yield
    hosted.store.cache_clear()


def stored(link):
    """The bytes behind one of this service's links."""
    return hosted.read_link(link)[1]


def call(name, arguments=None):
    async def go():
        async with Client(hosted.server) as client:
            return await client.call_tool(name, arguments or {})

    return anyio.run(go)


def public_dns(monkeypatch, address="93.184.216.34"):
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda host, port: [(None, None, None, "", (address, port))],
    )


# Storage and links


def test_saved_files_come_back_through_their_link():
    link = hosted.save("my song!.mid", b"MThd")
    assert link.startswith(f"{SITE}/files/") and link.endswith("/my-song-.mid")
    assert hosted.read_link(link) == ("my-song-.mid", b"MThd")


def test_expired_or_unknown_links_say_so(tmp_path):
    link = hosted.save("a.mid", b"x")
    old = hosted.time.time() - hosted.KEEP_SECONDS - 1
    for path in (tmp_path / "files").rglob("a.mid"):
        os.utime(path, (old, old))
    with pytest.raises(ScoreError, match="expired"):
        hosted.read_link(link)
    with pytest.raises(ScoreError, match="expired"):
        hosted.read_link(f"{SITE}/files/nothing/here.mid")


def test_names_are_made_safe():
    assert hosted._safe_name("../../etc/passwd") == "passwd"
    assert hosted._safe_name("...") == "score"
    with pytest.raises(ScoreError, match="not a music file"):
        hosted._music_name("notes.txt")


def fake_cloud_storage(monkeypatch):
    blobs = {}

    class NotFound(Exception):
        pass

    class Blob:
        def __init__(self, key):
            self.key = key

        def upload_from_string(self, data, content_type):
            blobs[self.key] = (data, content_type)

        def download_as_bytes(self):
            if self.key not in blobs:
                raise NotFound(self.key)
            return blobs[self.key][0]

    class Client:
        def bucket(self, name):
            assert name == "my-bucket"
            return types.SimpleNamespace(blob=Blob)

    google = types.ModuleType("google")
    cloud = types.ModuleType("google.cloud")
    storage = types.ModuleType("google.cloud.storage")
    exceptions = types.ModuleType("google.cloud.exceptions")
    storage.Client = Client
    exceptions.NotFound = NotFound
    google.cloud = cloud
    cloud.storage = storage
    for name, module in [
        ("google", google),
        ("google.cloud", cloud),
        ("google.cloud.storage", storage),
        ("google.cloud.exceptions", exceptions),
    ]:
        monkeypatch.setitem(sys.modules, name, module)
    return blobs


def test_cloud_storage_is_used_when_a_bucket_is_set(monkeypatch):
    blobs = fake_cloud_storage(monkeypatch)
    monkeypatch.setenv("SCOREPROOF_BUCKET", "my-bucket")
    hosted.store.cache_clear()
    link = hosted.save("tune.pdf", b"%PDF")
    assert isinstance(hosted.store(), hosted.CloudStore)
    assert list(blobs.values()) == [(b"%PDF", "application/pdf")]
    assert stored(link) == b"%PDF"
    assert hosted.store().get("missing/file.pdf") is None


# Fetching links from the web


def test_only_public_https_links_are_fetched(monkeypatch):
    with pytest.raises(ScoreError, match="https://"):
        hosted._check_public("http://example.com/a.mid")
    public_dns(monkeypatch, "169.254.169.254")
    with pytest.raises(ScoreError, match="public website"):
        hosted._check_public("https://metadata.example/a.mid")

    def no_such_host(host, port):
        raise socket.gaierror

    monkeypatch.setattr(socket, "getaddrinfo", no_such_host)
    with pytest.raises(ScoreError, match="Could not find"):
        hosted._check_public("https://nowhere.example/a.mid")


def test_redirects_are_checked_too(monkeypatch):
    request = urllib.request.Request("https://files.example/a.mid")
    handler = hosted._CheckedRedirects()
    public_dns(monkeypatch)
    moved = handler.redirect_request(
        request, None, 302, "Found", {}, "https://cdn.example/b.mid"
    )
    assert moved.full_url == "https://cdn.example/b.mid"
    public_dns(monkeypatch, "10.0.0.5")
    with pytest.raises(ScoreError, match="public website"):
        handler.redirect_request(
            request, None, 302, "Found", {}, "https://inside/b.mid"
        )


class FakeResponse:
    def __init__(self, data, url):
        self.data, self.url = data, url

    def read(self, limit):
        return self.data[:limit]

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def fake_web(monkeypatch, outcome):
    public_dns(monkeypatch)

    class Opener:
        def open(self, request, timeout):
            if isinstance(outcome, Exception):
                raise outcome
            return outcome

    monkeypatch.setattr(urllib.request, "build_opener", lambda *handlers: Opener())


def test_fetches_a_music_file_from_the_web(monkeypatch):
    fake_web(
        monkeypatch, FakeResponse(TUNE.encode(), "https://cdn.example/My%20Tune.abc")
    )
    assert hosted.read_link("https://files.example/x") == ("My-Tune.abc", TUNE.encode())


def test_web_downloads_that_fail_or_are_too_big(monkeypatch):
    fake_web(monkeypatch, OSError("timed out"))
    with pytest.raises(ScoreError, match="Could not download"):
        hosted.read_link("https://files.example/a.mid")
    monkeypatch.setattr(hosted, "MAX_BYTES", 4)
    fake_web(monkeypatch, FakeResponse(b"MThd and more", "https://files.example/a.mid"))
    with pytest.raises(ScoreError, match="10 MB"):
        hosted.read_link("https://files.example/a.mid")


# Running operations


def test_a_tool_needs_exactly_one_source(tmp_path):
    with pytest.raises(ScoreError, match="either file"):
        hosted.run_on(lambda path: {}, {})
    with pytest.raises(ScoreError, match="either file"):
        hosted.run_on(lambda path: {}, {"file": "x", "content": "y"})


def test_text_that_is_too_big_is_refused(monkeypatch):
    monkeypatch.setattr(hosted, "MAX_BYTES", 4)
    with pytest.raises(ScoreError, match="10 MB"):
        hosted.run_on(lambda path: {}, {"content": TUNE})


def test_results_name_inputs_link_outputs_and_hide_scratch_paths():
    def operation(path):
        made = path.with_suffix(".mid")
        made.write_bytes(b"MThd")
        if "fail" in path.read_text():
            raise ScoreError(f"Could not read {path}")
        return {
            "input": str(path),
            "outputs": [str(made)],
            "folder": str(path.parent),
            "note": "kept",
        }

    result = hosted.run_on(operation, {"content": TUNE, "filename": "waltz.abc"})
    assert result["input"] == "waltz.abc"
    assert stored(result["outputs"][0]) == b"MThd"
    assert result["folder"] == "0" and result["note"] == "kept"
    with pytest.raises(ScoreError) as error:
        hosted.run_on(operation, {"content": "fail", "filename": "x.abc"})
    assert str(error.value) == "Could not read 0/x.abc"


# The MCP connector


def test_connector_tools_and_instructions():
    async def go():
        async with Client(hosted.server) as client:
            return (await client.list_tools()).tools

    names = [tool.name for tool in anyio.run(go)]
    assert names == [
        "convert",
        "render",
        "inspect",
        "validate",
        "compare",
        "transpose",
        "extract",
        "list_formats",
    ]
    assert "upload" in hosted.INSTRUCTIONS


def test_write_music_as_abc_and_get_sheet_music():
    result = call("render", {"content": TUNE, "filename": "waltz.abc"})
    assert not result.is_error
    data = result.structured_content
    assert data["pages"] == 1
    assert stored(data["output"]).startswith(b"%PDF")


def test_convert_inspect_and_validate_text():
    converted = call("convert", {"to": "midi", "content": TUNE})
    assert converted.structured_content["confidence"] == "exact"
    assert stored(converted.structured_content["output"]).startswith(b"MThd")
    seen = call("inspect", {"content": TUNE, "bars": "1-2"}).structured_content
    assert seen["title"] == "Little Waltz" and seen["file"] == "tune.abc"
    assert call("validate", {"content": TUNE}).structured_content["file"] == "tune.abc"


def test_compare_transpose_and_extract_links():
    first = hosted.save("a.abc", TUNE.encode())
    second = hosted.save("b.abc", TUNE.replace("c2 B", "c2 A").encode())
    changes = call("compare", {"a": first, "b": second}).structured_content
    assert changes["identical_notes"] is False
    assert changes["a"] == "a.abc"
    moved = call("transpose", {"file": first, "key": "A"}).structured_content
    assert b"K:A" in stored(moved["output"])
    parts = call("extract", {"file": first, "bars": "1-2"}).structured_content
    assert stored(parts["files"][0]["output"]).startswith(b"X:")
    assert "convert" in call("list_formats").structured_content


def test_tool_errors_reach_the_client():
    result = call("convert", {"to": "midi"})
    assert result.is_error
    assert "either file" in result.content[0].text


# The web side


@pytest.fixture
def web():
    with TestClient(hosted.create_app()) as client:
        yield client


def test_drop_page_and_health(web):
    page = web.get("/")
    assert page.status_code == 200 and f"{SITE}/mcp" in page.text
    assert web.get("/health").json() == {"ok": True, "version": hosted.__version__}


def test_upload_then_convert_then_download(web):
    uploaded = web.post("/upload?name=waltz.abc", content=TUNE.encode()).json()
    assert uploaded["name"] == "waltz.abc" and uploaded["expires_in_hours"] == 24
    made = web.post("/api/convert", json={"file": uploaded["url"], "to": "pdf"}).json()
    path = made["output"].removeprefix(SITE)
    sheet = web.get(path)
    assert sheet.content.startswith(b"%PDF")
    assert sheet.headers["content-disposition"].startswith("inline")
    assert sheet.headers["x-content-type-options"] == "nosniff"
    original = web.get(uploaded["url"].removeprefix(SITE))
    assert original.headers["content-disposition"].startswith("attachment")


def test_upload_refusals(web, monkeypatch):
    assert web.post("/upload?name=page.html", content=b"x").status_code == 400
    assert web.post("/upload?name=a.mid", content=b"").status_code == 400
    monkeypatch.setattr(hosted, "MAX_BYTES", 4)
    assert web.post("/upload?name=a.mid", content=b"MThd more").status_code == 413


def test_convert_endpoint_refusals(web):
    assert web.post("/api/convert", content=b"nope").status_code == 400
    assert web.post("/api/convert", json={"to": "pdf"}).status_code == 400
    missing = web.post(
        "/api/convert", json={"file": f"{SITE}/files/x/y.mid", "to": "pdf"}
    )
    assert missing.status_code == 422 and "expired" in missing.json()["error"]


def test_expired_download(web):
    assert web.get("/files/nothing/here.pdf").status_code == 404


def test_mcp_over_http(web):
    response = web.post(
        "/mcp",
        json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
        headers={"accept": "application/json, text/event-stream"},
    )
    tools = response.json()["result"]["tools"]
    assert "render" in [tool["name"] for tool in tools]


def test_rate_limits_by_client_address(monkeypatch):
    app = hosted.create_app()
    app.limits = {"/upload": (2, 600)}
    web = TestClient(app)
    first = {"x-forwarded-for": "6.6.6.6, 1.2.3.4"}
    for _ in range(2):
        assert web.post("/upload?name=a.txt", headers=first).status_code == 400
    blocked = web.post("/upload?name=a.txt", headers=first)
    assert blocked.status_code == 429 and blocked.headers["retry-after"] == "60"
    other = {"x-forwarded-for": "6.6.6.6, 5.6.7.8"}
    assert web.post("/upload?name=a.txt", headers=other).status_code == 400
    assert web.get("/health", headers=first).status_code == 200


def test_rate_limit_forgets_idle_addresses(monkeypatch):
    limiter = hosted.RateLimit(app=None, limits={"/upload": (5, 10)})
    monkeypatch.setattr(limiter, "MAX_TRACKED", 1)
    limiter.seen[("1.1.1.1", "/upload")].append(-100.0)
    limiter.seen[("2.2.2.2", "/upload")].append(-100.0)
    assert limiter._allow("2.2.2.2", "/upload", 5, 10)
    assert list(limiter.seen) == [("2.2.2.2", "/upload")]


def test_client_address_without_a_proxy():
    assert hosted._client_ip({"headers": [], "client": ("9.9.9.9", 1)}) == "9.9.9.9"
    assert hosted._client_ip({"headers": []}) == "unknown"


def test_serve_starts_uvicorn(monkeypatch):
    started = {}
    import uvicorn

    monkeypatch.setattr(uvicorn, "run", lambda app, **options: started.update(options))
    assert cli.main(["serve", "--port", "9000"]) == 0
    assert started["port"] == 9000 and started["proxy_headers"] is True
