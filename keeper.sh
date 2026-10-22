#!/bin/bash
# The run loop. GitHub's schedule trigger is best-effort and skipped up to nine
# hours at a time, so one job stays up and checks Rightmove every 30 minutes
# itself, 06:00-23:30 London. Before the job's 6-hour limit it starts its own
# successor (workflow_dispatch from GITHUB_TOKEN is allowed to start runs), so
# the chain never depends on the schedule; the schedule only restarts a broken
# chain.
#
# Called by hunt.yml with config.py and state/state.json already unpacked.
# All hunter output goes to run.log, never to the public Actions log.
set -uo pipefail

BUDGET_MIN="${KEEPER_BUDGET_MIN:-280}"   # then hand over; + a 60 min check + retries stays under the 355 min job limit
SLOTS="5 35"                             # minutes past the hour (London)
FIRST_HOUR=6; LAST_HOUR=23
start=$(date +%s)
fails=0

london() { TZ=Europe/London date "$@"; }
elapsed_min() { echo $(( ($(date +%s) - start) / 60 )); }

# Seconds until the next :05/:35 slot inside active hours.
next_slot_in() {
  python3 - "$SLOTS" "$FIRST_HOUR" "$LAST_HOUR" <<'PY'
import sys, datetime as dt, zoneinfo
slots = sorted(int(s) for s in sys.argv[1].split()); first, last = int(sys.argv[2]), int(sys.argv[3])
now = dt.datetime.now(zoneinfo.ZoneInfo("Europe/London"))
t = now.replace(second=0, microsecond=0)
while True:
    t += dt.timedelta(minutes=1)
    if t.minute in slots and first <= t.hour <= last:
        print(int((t - now).total_seconds()) + 2); break  # +2: never wake just before the slot
PY
}

save_state() {
  [ -f state/state.json ] || return 0
  if [ -f "$RUNNER_TEMP/state.sha" ] && sha256sum -c --quiet "$RUNNER_TEMP/state.sha" 2>/dev/null; then return 0; fi
  ./crypt.sh enc state/state.json state/state.json.enc
  git add state/state.json.enc
  git diff --cached --quiet || git commit -q -m "state $(date -u +%FT%TZ)"
  for i in 1 2 3 4 5; do
    if git pull -q --rebase && git push -q; then
      sha256sum state/state.json > "$RUNNER_TEMP/state.sha"; return 0
    fi
    sleep $((i * 10))
  done
  echo "::warning::state push failed; will retry after the next check"
}

warn_telegram() {
  [ -n "${TELEGRAM_BOT_TOKEN:-}" ] && [ -n "${TELEGRAM_CHAT_ID:-}" ] || return 0
  curl -s -o /dev/null --max-time 30 "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/sendMessage" \
    --data-urlencode "chat_id=${TELEGRAM_CHAT_ID}" --data-urlencode "text=$1" || true
}

check() {
  local h; h=$((10#$(london +%H)))
  if [ "$h" -lt "$FIRST_HOUR" ]; then return 0; fi
  echo "===== check $(london '+%F %H:%M %Z')" >> run.log
  timeout 60m python hunter.py >> run.log 2>&1
  local rc=$?
  echo "===== exit $rc" >> run.log
  save_state
  if [ "$rc" -eq 0 ]; then
    echo "check $(date -u +%H:%MZ): ok"; fails=0
  else
    fails=$((fails + 1)); echo "::warning::check $(date -u +%H:%MZ) exited $rc ($fails in a row)"
    # Once per streak, so a broken run doesn't go unnoticed for days.
    [ "$fails" -eq 3 ] && warn_telegram "⚠️ Flat hunter: the last 3 checks failed. Run ./logs.sh on the Mac to see why."
  fi
}

start_successor() {
  for i in 1 2 3 4 5 6; do
    if gh workflow run hunt.yml --ref main -f chain=1 >/dev/null 2>&1; then
      echo "successor started"; return 0
    fi
    sleep $((i * 20))
  done
  echo "::error::could not start the successor; the backup schedule will restart the chain"
  return 1
}

check   # run straight away: whatever started this job may have followed a gap
while :; do
  wait_s=$(next_slot_in)
  left_s=$(( (BUDGET_MIN - $(elapsed_min)) * 60 ))
  if [ "$wait_s" -gt "$left_s" ]; then
    # Next slot is past this job's budget (e.g. overnight): sleep out the budget, then hand over.
    [ "$left_s" -gt 0 ] && sleep "$left_s"
    break
  fi
  sleep "$wait_s"
  check
done
start_successor
