#!/bin/bash
# Run logs are encrypted (the repo is public). Print the log of a run:
#   ./logs.sh            latest run
#   ./logs.sh <run-id>
set -euo pipefail
REPO=yalzerboy/flat-hunter
RUN="${1:-$(gh run list -R "$REPO" -L 1 --json databaseId --jq '.[0].databaseId')}"
TMP="$(mktemp -d)"; trap 'rm -rf "$TMP"' EXIT
gh run download "$RUN" -R "$REPO" -n run-log -D "$TMP"
"$(dirname "$0")/crypt.sh" dec "$TMP/run.log.enc" "$TMP/run.log"
cat "$TMP/run.log"
