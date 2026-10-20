#!/bin/bash
# Paste the token from `claude setup-token`; it is tested before being saved to GitHub.
set -e
REPO="${1:-yalzerboy/flat-hunter}"
read -rs -p "Paste the token from 'claude setup-token' (hidden), then press Enter: " RAW; echo
TOKEN="$(printf '%s' "$RAW" | tr -d ' \n\r\t')"
case "$TOKEN" in
  sk-ant-oat01-*) ;;
  *) echo "That doesn't look like a setup-token (should start with sk-ant-oat01-). Got ${#TOKEN} chars starting '${TOKEN:0:6}'."; exit 1;;
esac
echo "Testing (${#TOKEN} chars)..."
if OUT="$(CLAUDE_CODE_OAUTH_TOKEN="$TOKEN" claude -p --model haiku "reply with just: ok" 2>&1)" && [[ "$OUT" == *ok* ]]; then
  printf '%s' "$TOKEN" | gh secret set CLAUDE_CODE_OAUTH_TOKEN -R "$REPO"
  echo "Works, and saved to $REPO."
else
  echo "Claude rejected it: $OUT"
  echo "Run 'claude setup-token' again and copy the whole token (it can wrap over several lines)."
  exit 1
fi
