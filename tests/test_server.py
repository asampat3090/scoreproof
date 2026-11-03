"""Tests for the MCP server, through a real in-process MCP client."""

import json

import anyio
import pytest
from mcp.client import Client

from scoreproof import server

TOOLS = [
    "convert",
    "batch_convert",
    "inspect",
    "validate",
    "compare",
    "transpose",
    "extract",
    "render",
    "list_formats",
]


def call(name, arguments=None):
    async def go():
        async with Client(server.server) as client:
            return await client.call_tool(name, arguments or {})

    return anyio.run(go)


def test_lists_every_tool_with_safety_hints():
    async def go():
        async with Client(server.server) as client:
            return (await client.list_tools()).tools

    tools = {t.name: t for t in anyio.run(go)}
    assert list(tools) == TOOLS
    assert tools["inspect"].annotations.read_only_hint is True
    assert tools["convert"].annotations.destructive_hint is False


def test_convert_returns_a_structured_report(chorale_file):
    result = call("convert", {"input": str(chorale_file), "to": "midi"})
    assert not result.is_error
    report = result.structured_content
    assert report["confidence"] == "exact"
    assert report["output"].endswith("chorale.mid")


def test_errors_reach_the_client_in_plain_words(tmp_path):
    result = call("convert", {"input": str(tmp_path / "nope.xml"), "to": "abc"})
    assert result.is_error
    assert "File not found" in result.content[0].text


@pytest.mark.parametrize(
    "name, arguments, key",
    [
        ("inspect", {"bars": "1-2", "parts": ["Bass"]}, "excerpt"),
        ("inspect", {"bars": ""}, "parts"),
        ("validate", {}, "status"),
        ("transpose", {"key": "G minor"}, "to_key"),
        ("extract", {"each_part": True}, "files"),
        ("render", {"to": "svg"}, "pages"),
    ],
)
def test_tools_on_a_real_score(chorale_file, name, arguments, key):
    result = call(name, {"input": str(chorale_file), **arguments})
    assert not result.is_error, result.content
    assert key in result.structured_content


def test_inspect_with_no_bars_leaves_out_the_excerpt(chorale_file):
    result = call("inspect", {"input": str(chorale_file), "bars": ""})
    assert "excerpt" not in result.structured_content


def test_batch_and_compare(chorale_file, tmp_path):
    out = tmp_path / "out"
    batch = call(
        "batch_convert",
        {"inputs": [str(tmp_path)], "to": "abc", "output_dir": str(out)},
    )
    assert batch.structured_content["converted"] == 1
    abc = batch.structured_content["files"][0]["output"]
    compared = call("compare", {"a": str(chorale_file), "b": abc})
    assert compared.structured_content["identical_notes"] is True


def test_list_formats():
    formats = call("list_formats").structured_content
    assert [f["format"] for f in formats["render"]] == ["pdf", "png", "svg", "wav"]


def test_convert_to_sheet_music(chorale_file):
    result = call("convert", {"input": str(chorale_file), "to": "pdf"})
    assert result.structured_content["output"].endswith("chorale.pdf")
    arguments = {"input": str(chorale_file), "to": "png", "page_size": "a4"}
    rendered = call("render", arguments).structured_content
    assert rendered["page_size"] == "a4"


def test_resources_and_prompts():
    async def go():
        async with Client(server.server) as client:
            resources = [str(r.uri) for r in (await client.list_resources()).resources]
            repairs = await client.read_resource("scoreproof://repairs")
            formats = await client.read_resource("scoreproof://formats")
            prompts = [p.name for p in (await client.list_prompts()).prompts]
            texts = [
                (await client.get_prompt(p, {"path": "/x/song.mid"})).messages[0]
                for p in prompts
            ]
            return resources, repairs, formats, prompts, texts

    resources, repairs, formats, prompts, texts = anyio.run(go)
    assert resources == ["scoreproof://formats", "scoreproof://repairs"]
    rules = [r["rule"] for r in json.loads(repairs.contents[0].text)]
    assert "fill_note_free_bars" in rules
    assert "convert" in json.loads(formats.contents[0].text)
    assert prompts == ["midi_to_score", "make_parts", "check_export"]
    assert all("/x/song.mid" in m.content.text for m in texts)


def test_main_runs_over_stdio(monkeypatch):
    calls = []
    monkeypatch.setattr(server.server, "run", lambda transport: calls.append(transport))
    server.main()
    assert calls == ["stdio"]
