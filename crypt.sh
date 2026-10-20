#!/bin/bash
# Encrypt/decrypt a file with STATE_KEY (env) or ~/.config/flat-hunter/state_key.
#   ./crypt.sh enc state/state.json state/state.json.enc
#   ./crypt.sh dec state/state.json.enc state/state.json
set -euo pipefail
KEY="${STATE_KEY:-$(cat ~/.config/flat-hunter/state_key)}"
case "$1" in
  enc) openssl enc -aes-256-cbc -pbkdf2 -iter 200000 -salt -pass fd:3 -in "$2" -out "$3" 3<<<"$KEY" ;;
  dec) openssl enc -d -aes-256-cbc -pbkdf2 -iter 200000 -pass fd:3 -in "$2" -out "$3" 3<<<"$KEY" ;;
  *) echo "usage: $0 enc|dec IN OUT"; exit 2 ;;
esac
