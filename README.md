# Flat hunter

Watches Rightmove, OnTheMarket, Zoopla and OpenRent every 30 minutes (GitHub Actions),
drops only listings that objectively break a dealbreaker, has Claude read every remaining
listing (text, floorplan, photos) and posts to a Telegram group:

- **Instantly:** score 7+/10 with confirmed outdoor space.
- **6pm digest:** near misses, best first, plus what the hard filters removed
  (so you can check they're not throwing away good flats).
- **Price drops** on flats that scored 6+.

## Sites
Each site is an adapter in `sources.py` that turns its search and listing pages into one
shape, so filtering, commutes, scoring and messages are the same for all of them.

- **Rightmove, OnTheMarket:** read directly.
- **Zoopla, OpenRent:** both refuse GitHub's servers (403 / 405, whatever the browser
  fingerprint), so on Actions their pages go through a residential proxy in the `PROXY_URL`
  secret (`http://user:pass@host:port`). Without it they are skipped there. Photos come from
  CDNs that don't block, so they never use the proxy. From a home connection both work direct.
- **The same flat on two sites** (same bedrooms, rent within 2%, map pins within 250 m) is
  scored once. The copy is listed in the 6pm digest with both links, so a wrong match is visible.
- **A site's first run** scores what it listed in the last 3 days and records the rest.
- One site failing doesn't stop the others; after 3 hours of failures it posts a warning.

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
optional `TFL_APP_KEY`, optional `PROXY_URL` (Zoopla + OpenRent).

## Scoring
Two-stage via the Claude Code CLI on a Claude subscription
(`CLAUDE_CODE_OAUTH_TOKEN` from `claude setup-token`): Haiku reads every listing,
Opus re-reads any Haiku scores 5+ or is unsure about outdoor space.

## Manual runs
Actions tab > Flat hunt > Run workflow, with args like `--backfill 3`, `--digest-now`,
`--only zp:66358330` (score one listing and post it; Rightmove ids are bare, others are
`otm:`, `zp:`, `or:`) or `--sources otm,zp`.

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
