#!/usr/bin/env bash
# Build the ZIP to upload at platform.openai.com/plugins.
#   chatgpt/build.sh            -> dist/scoreproof-chatgpt.zip
set -euo pipefail
cd "$(dirname "$0")"
if grep -q REPLACE_WITH_DEMO_VIDEO_URL plugin.json; then
  echo "Set review.demo_recording_url in chatgpt/plugin.json first" >&2
  exit 1
fi
mkdir -p ../dist
rm -f ../dist/scoreproof-chatgpt.zip
zip -qr ../dist/scoreproof-chatgpt.zip plugin.json mcp.json skills assets
echo "dist/scoreproof-chatgpt.zip"
