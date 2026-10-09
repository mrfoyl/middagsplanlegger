#!/usr/bin/env bash
# Klon mcp-oda på fast commit, legg på våre patcher (branch "middag") og bygg.
# Trygt å kjøre flere ganger.
set -euo pipefail
cd "$(dirname "$0")/.."

UPSTREAM=https://github.com/agfagerbakk/mcp-oda.git
PIN=62f0b64eb6aa5c1ce5c7bb9dd1054997486a9cbd

if [ ! -d vendor/mcp-oda/.git ]; then
  git clone -q "$UPSTREAM" vendor/mcp-oda
fi
cd vendor/mcp-oda
if ! git rev-parse --verify -q middag >/dev/null; then
  git checkout -q -b middag "$PIN"
fi
git checkout -q middag
# Legg på patcher som mangler. En patch regnes som lagt på hvis den kan
# reverseres rent – da fungerer dette også på eksisterende installasjoner
# når nye patcher kommer til i patches/.
for patch in ../../patches/*.patch; do
  if git apply --reverse --check "$patch" 2>/dev/null; then
    continue
  fi
  echo "Legger på $(basename "$patch")"
  git -c user.name=middag -c user.email=middag@localhost am -q "$patch"
done
npm ci --no-audit --no-fund
npm run build
echo "mcp-oda klar: $(pwd)/dist/index.js"
