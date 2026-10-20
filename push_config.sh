#!/bin/bash
# The real config.py never goes in git. After editing it, run this to update the
# CONFIG_PY secret the workflow runs with.
set -euo pipefail
REPO="${1:-yalzerboy/flat-hunter}"
python3 -c "import ast,sys; ast.parse(open('config.py').read())"
base64 < config.py | tr -d '\n' | gh secret set CONFIG_PY -R "$REPO"
echo "config.py saved to $REPO as CONFIG_PY"
