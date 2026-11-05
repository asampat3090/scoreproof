# Changelog

## 0.2.0 (2026-11-05)

### New: run Scoreproof as a web service
- `scoreproof serve` starts a remote MCP connector at `/mcp`, plus a drop page at `/` where people can convert files in the browser. Install it with `pip install "scoreproof[hosted]"`.
- It works from claude.ai, the Claude desktop and mobile apps, and ChatGPT. The tools take a link to a file, or the music as text (such as ABC), and return download links.
- Built for public hosting: per-address rate limits, a 10 MB file limit, and downloads only from public https addresses. Files are kept in Cloud Storage or a local folder and deleted after 24 hours.
- Includes a Dockerfile and `deploy/cloudrun.sh`, a one-command, re-runnable Cloud Run deploy.
- A free public instance runs at https://scoreproof-fkkuctjokq-uc.a.run.app. Add `/mcp` to that address to use it as a connector.

### Fixed
- Sheet music made from ABC files no longer comes out without clefs. Each part now gets the clef that suits its range, treble or bass.

### Also
- A Claude Code plugin: `/plugin install scoreproof --marketplace asampat3090/scoreproof`.
- The MCP registry listing now includes the hosted connector as well as the local `uvx` server.

## 0.1.0

First release.
