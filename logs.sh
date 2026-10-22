#!/bin/bash
# Run logs are encrypted (the repo is public). Print the log of a run:
#   ./logs.sh            latest finished run that has a log
#   ./logs.sh <run-id>
# A loop run uploads its log when it hands over (every ~5 hours), so the
# run that is still going has no log yet.
set -euo pipefail
REPO=yalzerboy/flat-hunter
RUN="${1:-$(gh api "repos/$REPO/actions/artifacts?name=run-log&per_page=1" --jq '.artifacts[0].workflow_run.id')}"
TMP="$(mktemp -d)"; trap 'rm -rf "$TMP"' EXIT
gh run download "$RUN" -R "$REPO" -n run-log -D "$TMP"
"$(dirname "$0")/crypt.sh" dec "$TMP/run.log.enc" "$TMP/run.log"
cat "$TMP/run.log"
