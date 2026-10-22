# Flat hunter

Watches a Rightmove search every 30 minutes (GitHub Actions), drops only listings
that objectively break a dealbreaker, has Claude read every remaining listing
(text, floorplan, photos) and posts to a Telegram group:

- **Instantly:** score 7+/10 with confirmed outdoor space.
- **6pm digest:** near misses, best first, plus what the hard filters removed
  (so you can check they're not throwing away good flats).
- **Price drops** on flats that scored 6+.

## Hard filters (only when the listing says so explicitly)
- Basement / lower ground floor flat
- Short let / holiday let / students only
- Commute over the hard limit (TfL best route, exact map pins only)

Everything else is ranking only - the AI never removes a flat.

## Public repo, private data
Nothing personal is committed in the clear:
- `config.py` (search, names, workplaces, preferences) is gitignored and lives in the
  `CONFIG_PY` secret. Start from `config.example.py`; after editing, run `./push_config.sh`.
- `state/state.json.enc` is the state, encrypted with the `STATE_KEY` secret
  (local copy of the key: `~/.config/flat-hunter/state_key`).
  `./crypt.sh dec state/state.json.enc state/state.json` to read it.
- Run output goes to an encrypted artifact, not the public log: `./logs.sh [run-id]`.

Secrets: `CONFIG_PY`, `STATE_KEY`, `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`
(`python setup_telegram.py`), `CLAUDE_CODE_OAUTH_TOKEN` (`./setup_claude.sh`),
optional `TFL_APP_KEY`.

## Scoring
Two-stage via the Claude Code CLI on a Claude subscription
(`CLAUDE_CODE_OAUTH_TOKEN` from `claude setup-token`): Haiku reads every listing,
Opus re-reads any Haiku scores 5+ or is unsure about outdoor space.

## Manual runs
Actions tab > Flat hunt > Run workflow, with args like `--backfill 3` or `--digest-now`.

## Schedule
GitHub's own `schedule:` trigger is best-effort and skipped up to nine hours at a time, so
it is not the clock. Instead one job stays up and runs a check itself at :05 and :35,
06:05-23:35 London (`keeper.sh`). Before the job's 6-hour limit it starts its own successor
with `workflow_dispatch` (GITHUB_TOKEN may start dispatch runs), so the chain runs with no
outside service and no extra token. The repo is public, so the minutes are free.

- The `schedule:` (`17,47 * * * *`) only restarts a broken chain: a scheduled run that finds a
  loop running or waiting exits in seconds.
- `concurrency` stops two loops overlapping; a duplicate just waits or is dropped.
- After 3 failed checks in a row the bot posts a warning to the Telegram group.
- A check after a gap catches up by itself: it walks newest-first until a whole page of
  listings is already known.
- One-off runs: `gh workflow run hunt.yml -f args="--digest-now"`. With empty args a manual
  run starts the loop (use it to restart the chain by hand).
- `./logs.sh` reads the log of the last loop that handed over; the loop that is running
  uploads its log when it hands over.
