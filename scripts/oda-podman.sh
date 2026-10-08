#!/usr/bin/env bash
# Kjør mcp-oda i en Node-container – for maskiner uten Node (f.eks. Bazzite).
# Sesjonen (cookies) lagres i ~/.mcp-oda, akkurat som ved vanlig kjøring.
set -euo pipefail
DIR="$(cd "$(dirname "$0")/.." && pwd)"
mkdir -p "$HOME/.mcp-oda"
chmod 700 "$HOME/.mcp-oda"
if command -v podman >/dev/null; then
  RUN=(podman)
else
  RUN=(flatpak-spawn --host podman)  # fra innsiden av en flatpak, f.eks. VS Code
fi
exec "${RUN[@]}" run --rm -i \
  -v "$DIR/vendor/mcp-oda:/app:Z,ro" \
  -v "$HOME/.mcp-oda:/data:Z" \
  -w /app docker.io/library/node:24-slim \
  node dist/index.js --data-dir /data "$@"
