#!/usr/bin/env node
const { version } = require("../package.json");

if (["--version", "-V"].includes(process.argv[2])) {
  console.log(version);
} else {
  console.log(`scoreproof ${version}

The verified music-format converter (MusicXML, MIDI, ABC, MEI, Humdrum)
and its MCP server are in development and arrive in 0.1.0.
Follow along: https://github.com/asampat3090/songchain`);
}
