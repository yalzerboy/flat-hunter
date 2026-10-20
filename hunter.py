"""Flat hunter: watch a Rightmove search, filter, score with Claude, post to Telegram.

Run modes:
  python hunter.py                 normal run (what the cron does)
  python hunter.py --backfill 3    treat listings from the last 3 days as new
  python hunter.py --dry-run       print messages instead of sending them
  python hunter.py --digest-now    send the daily digest immediately
"""

import argparse
import datetime as dt
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from html import escape as html_escape
from pathlib import Path
from typing import Literal
from zoneinfo import ZoneInfo

from pydantic import BaseModel, Field

import config

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/129.0 Safari/537.36")
LONDON = ZoneInfo("Europe/London")
STATE_PATH = Path(__file__).parent / "state" / "state.json"
MAX_PAGES = 12          # 24 per page; a normal 30-min run needs 1
POLITE_DELAY = 1.5      # seconds between Rightmove requests
MAX_NEW_PER_RUN = 60    # a normal 30-min window has ~2; more means stale state


# --------------------------------------------------------------------- HTTP

def get(url, headers=None, binary=False, tries=3):
    for attempt in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA, **(headers or {})})
            with urllib.request.urlopen(req, timeout=30) as r:
                data = r.read()
                return data if binary else data.decode("utf-8", "replace")
        except urllib.error.HTTPError as e:
            if e.code in (404, 410):
                raise
            if attempt == tries - 1:
                raise
        except urllib.error.URLError:
            if attempt == tries - 1:
                raise
        time.sleep(3 * (attempt + 1))


# --------------------------------------------------------------- Rightmove

def search_page(index):
    html = get(config.SEARCH_URL + f"&index={index}")
    m = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', html, re.S)
    if not m:
        raise RuntimeError("Rightmove search page changed shape (no __NEXT_DATA__)")
    sr = json.loads(m.group(1))["props"]["pageProps"]["searchResults"]
    return sr["properties"], int(str(sr["resultCount"]).replace(",", ""))


def page_model(html):
    """Listing pages ship their data as a reference-encoded array; inflate it."""
    marker = "window.__PAGE_MODEL = "
    i = html.find(marker)
    if i < 0:
        return None
    pm, _ = json.JSONDecoder().raw_decode(html, i + len(marker))
    arr = json.loads(pm["data"])
    memo = {}

    def dec(i):
        if i in memo:
            return memo[i]
        if i < 0:
            return None
        v = arr[i]
        if isinstance(v, dict):
            o = memo[i] = {}
            for k, j in v.items():
                o[k] = dec(j)
            return o
        if isinstance(v, list):
            o = memo[i] = []
            o.extend(dec(j) for j in v)
            return o
        return v

    return dec(0)


def listing_detail(pid):
    pm = page_model(get(f"https://www.rightmove.co.uk/properties/{pid}"))
    if not pm or "propertyData" not in pm:
        raise RuntimeError(f"listing {pid}: no page model")
    return pm["propertyData"]


def monthly(p):
    amt, freq = p["price"]["amount"], (p["price"].get("frequency") or "monthly").lower()
    if freq == "weekly":
        return round(amt * 52 / 12)
    if freq == "daily":
        return round(amt * 365 / 12)
    return amt


def strip_html(s):
    s = re.sub(r"<br\s*/?>", "\n", s or "")
    s = re.sub(r"<[^>]+>", "", s)
    return re.sub(r"\n{3,}", "\n\n", s).strip()


# ------------------------------------------------------------ Hard filters
#
# Only objective facts, only when the listing says so explicitly. Anything
# ambiguous is passed through (with a note for the AI and the digest).

BASEMENT_STRICT = re.compile(
    r"\b(lower[- ]ground|basement|semi[- ]basement)(?:[- ]floor)?\s+"
    r"(flat|apartment|maisonette|conversion|garden flat)\b"
    r"|\b(on|occupies|occupying|set on|located on|situated on|positioned on|across)\s+the\s+"
    r"(lower[- ]ground|basement|semi[- ]basement)(\s+floor|\s+level)?\b",
    re.I,
)
BASEMENT_LOOSE = re.compile(r"\b(lower[- ]ground|basement)\b", re.I)
SHORT_LET = re.compile(
    r"\b(short[- ]let|short[- ]term let|holiday let|students only|student only|"
    r"student accommodation|student let)\b", re.I)


def hard_filter(text, lettings, his_minutes, pin_exact):
    """Return (reason or None, notes for the AI)."""
    notes = []
    if BASEMENT_STRICT.search(text):
        return "basement / lower ground (stated in listing)", notes
    if BASEMENT_LOOSE.search(text):
        notes.append("Listing mentions 'basement' or 'lower ground' but not clearly as the "
                     "flat's own floor (could be a gym, bike store or another flat).")

    let_type = (lettings or {}).get("letType") or ""
    if "short" in let_type.lower():
        return f"short let (let type: {let_type})", notes
    if SHORT_LET.search(text):
        return f"short let / student-only ('{SHORT_LET.search(text).group(0)}')", notes
    # NOT filtering on minimumTermInMonths: normal 12-month lets often say 3 or 6
    # (audit 16 Oct 2026: a 7th-floor 2-double-bed flat with balcony said 3).

    if his_minutes is not None and his_minutes > config.HIS_HARD_LIMIT_MINUTES and pin_exact:
        return f"{config.HIS_NAME} commute {his_minutes} min (limit {config.HIS_MAX_MINUTES})", notes
    return None, notes


# ------------------------------------------------------------------- TfL

def next_weekday():
    d = dt.datetime.now(LONDON).date() + dt.timedelta(days=1)
    while d.weekday() >= 5:
        d += dt.timedelta(days=1)
    return d.strftime("%Y%m%d")


def short_place(s):
    for junk in (" Rail Station", " Underground Station", " Station", " International"):
        s = s.replace(junk, "")
    return s


def tfl_best(frm, to, at, modes=None):
    """Fastest TfL journey as (total minutes, [(leg text, minutes), ...]) or (None, [])."""
    params = {"date": next_weekday(), "time": at, "timeIs": "Departing"}
    if modes:
        params["mode"] = modes
    if os.environ.get("TFL_APP_KEY"):
        params["app_key"] = os.environ["TFL_APP_KEY"]
    url = f"https://api.tfl.gov.uk/Journey/JourneyResults/{frm}/to/{to}?" + urllib.parse.urlencode(params)
    try:
        d = json.loads(get(url))
    except Exception as e:  # never let a TfL hiccup drop a flat
        print(f"  TfL failed ({e})", file=sys.stderr)
        return None, []
    js = d.get("journeys") or []
    if not js:
        return None, []
    best = min(js, key=lambda j: j["duration"])
    legs = []
    for l in best["legs"]:
        text = short_place(l["instruction"]["summary"])
        legs.append((text, l["duration"]))
    return best["duration"], legs


def bike_minutes(lat1, lon1, lat2, lon2):
    url = (f"https://routing.openstreetmap.de/routed-bike/route/v1/driving/"
           f"{lon1},{lat1};{lon2},{lat2}?overview=false")
    try:
        r = json.loads(get(url))["routes"][0]
        return round(r["duration"] / 60 * config.BIKE_SLOWDOWN)
    except Exception as e:
        print(f"  bike routing failed ({e})", file=sys.stderr)
        return None


def km(lat1, lon1, lat2, lon2):
    import math
    dx = (lon2 - lon1) * 69.0 * math.cos(math.radians(lat1)) * 1.609
    dy = (lat2 - lat1) * 69.0 * 1.609
    return math.hypot(dx, dy)


def his_commute(lat, lon):
    total, legs = tfl_best(f"{lat},{lon}", config.HIS_WORK, config.HIS_DEPART)
    if legs and legs[-1][0].lower().startswith("walk to e1"):
        legs[-1] = (f"Walk to {config.HIS_WORK_NAME}", legs[-1][1])
    return total, legs


def hockey_commutes(lat, lon):
    """[(venue, minutes or None), ...] for every hockey venue."""
    return [(name, tfl_best(f"{lat},{lon}", to, config.HOCKEY_DEPART)[0])
            for name, to in config.HOCKEY_VENUES]


_train_cache = {}


def train_to_her_station(name, slat, slon):
    if name not in _train_cache:
        _train_cache[name] = tfl_best(f"{slat},{slon}", config.HER_STATION, config.HER_DEPART,
                                      modes="national-rail,walking")
    return _train_cache[name]


def her_commute(lat, lon):
    """Fastest of: public transport to her station, or folding bike to a Thameslink
    station in London then train. Either way she bikes the last stretch to work."""
    last = (f"🚲 Bike to {config.HER_WORK_NAME}", config.HER_LAST_MILE_BIKE_MIN)
    options = []

    total, legs = tfl_best(f"{lat},{lon}", config.HER_STATION, config.HER_DEPART)
    if total:
        options.append((total + last[1], legs + [last]))

    near = sorted(config.THAMESLINK_STATIONS, key=lambda s: km(lat, lon, s[1], s[2]))[:3]
    for name, slat, slon in near:
        if km(lat, lon, slat, slon) > config.BIKE_TO_STATION_MAX_KM:
            continue
        ride = bike_minutes(lat, lon, slat, slon)
        train, tlegs = train_to_her_station(name, slat, slon)
        if ride is None or not train:
            continue
        tlegs = list(tlegs)
        walk = 0
        while tlegs and tlegs[0][0].lower().startswith("walk"):  # station-entrance walk
            walk += tlegs.pop(0)[1]
        # TfL's walk is a pedestrian's interchange allowance; she wheels the folded bike straight in.
        extra = min(walk, config.STATION_ENTRY_MAX_MIN)
        legs = [(f"🚲 Bike to {name}", ride + config.FOLD_BUFFER_MIN + extra)] + tlegs + [last]
        options.append((ride + config.FOLD_BUFFER_MIN + extra + train - walk + last[1], legs))

    if not options:
        return None, []
    return min(options, key=lambda o: o[0])


def legs_text(legs, total=None):
    text = " → ".join(f"{t} {m}m" for t, m in legs)
    wait = (total or 0) - sum(m for _, m in legs)
    if wait >= 3:
        text += f" (+{wait}m waiting)"
    return text


# ------------------------------------------------------------- AI scoring
#
# Scoring runs through the Claude Code CLI (`claude -p`) so it bills to the
# Claude subscription (CLAUDE_CODE_OAUTH_TOKEN from `claude setup-token`)
# rather than a pay-as-you-go API key. Two stages: a cheap model reads every
# listing; a strong model re-reads the ones worth a second look.

Outdoor = Literal["private_balcony", "terrace", "roof_terrace", "private_garden",
                  "communal_garden", "juliet_balcony_only", "none_visible", "unclear"]


class Score(BaseModel):
    score: int = Field(description="1-10 fit for us. 7+ means worth booking a viewing.")
    outdoor_space: Outdoor
    outdoor_evidence: str = Field(description="Where you saw it: text, floorplan, or photo number.")
    second_bedroom: Literal["double", "small_double", "single_only", "box_room", "unclear"]
    second_bedroom_size: str = Field(description="From the floorplan if shown, e.g. 10'2 x 9'6, else empty.")
    light: Literal["excellent", "good", "average", "poor", "unclear"]
    ceilings: Literal["high", "normal", "low", "unclear"]
    living_room: Literal["large_open", "good", "small", "none", "unclear"] = Field(
        description="large_open = a big, open living/reception space, e.g. 20ft+ or 25 sq m+ open-plan.")
    furnished: Literal["unfurnished", "furnished", "either", "part", "unknown"]
    floor: str = Field(description="Floor level if stated or visible, else 'unknown'.")
    lift: Literal["yes", "no", "unknown"]
    highlights: list[str] = Field(description="Up to 3 short positives.")
    concerns: list[str] = Field(description="Up to 3 short negatives or things to check at a viewing.")
    one_liner: str = Field(description="One sentence, under 20 words, why this flat is or isn't a fit.")


SCORE_SCHEMA = json.dumps(Score.model_json_schema())

INSTRUCTIONS = f"""You help a couple find a London rental flat. Judge ONE listing against
their preferences. Use the Read tool to open EVERY image file listed below (floorplans
first, then photos) before answering. Then return the structured result.

Their preferences:
{config.PREFERENCES}

How to judge:
- You only RANK; nothing you say removes a listing. Be honest, not harsh.
- Missing information is "unclear", not bad. Do not mark a flat down because the agent
  wrote a short description; look at the photos and floorplan.
- Outdoor space: check the text, the floorplan (balcony/terrace drawn and labelled) AND
  the photos. Only say private_balcony / terrace if you actually see or read it. A
  railing across a full-height window with no floor to stand on is a Juliet balcony.
- Second bedroom: will mainly be a study. Use floorplan dimensions when given (double is
  roughly 11ft x 9ft+ or 10 sq m+; single-only is under about 7 sq m).
- Rent: their real budget is the budget_pcm in the facts. Above it, only an exceptional flat
  should score 9+; mark ordinary over-budget flats down.
- Refer to photos by number when citing evidence.
"""


def download_images(pd, folder):
    paths = []
    for n, fp in enumerate((pd.get("floorplans") or [])[:3], 1):
        if fp.get("type", "IMAGE") == "IMAGE" and fp.get("url"):
            paths.append((f"Floorplan {n}", fp["url"], folder / f"floorplan{n}.jpeg"))
    for n, im in enumerate((pd.get("images") or [])[:config.MAX_PHOTOS], 1):
        url = (im.get("resizedImageUrls") or {}).get(config.PHOTO_SIZE) or im.get("url")
        paths.append((f"Photo {n}", url, folder / f"photo{n:02d}.jpeg"))
    out = []
    for label, url, path in paths:
        try:
            path.write_bytes(get(url, binary=True))
            out.append((label, path))
        except Exception as e:
            print(f"  image fetch failed ({label}): {e}", file=sys.stderr)
    return out


def ensure_claude():
    """Install the Claude Code CLI on first use, so runs with nothing new stay fast."""
    if shutil.which("claude"):
        return
    print("Installing Claude Code CLI...")
    subprocess.run(["npm", "install", "-g", "--silent", "@anthropic-ai/claude-code"], check=True)


def ask_claude(model, effort, prompt, cwd):
    ensure_claude()
    cmd = ["claude", "-p", "--model", model, "--effort", effort,
           "--tools", "Read", "--allowedTools", "Read",
           "--no-session-persistence", "--output-format", "json",
           "--json-schema", SCORE_SCHEMA, prompt]
    try:
        r = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=420)
    except subprocess.TimeoutExpired:
        print(f"  claude ({model}) timed out", file=sys.stderr)
        return None
    try:
        d = json.loads(r.stdout)
    except json.JSONDecodeError:
        print(f"  claude ({model}) bad output: {r.stdout[:300]} {r.stderr[:300]}", file=sys.stderr)
        return None
    if d.get("is_error"):
        msg = str(d.get("result"))[:300]
        print(f"  claude ({model}) error: {msg}", file=sys.stderr)
        if "authenticate" in msg.lower() or "limit" in msg.lower():
            raise ClaudeUnavailable(msg)
        return None
    out = d.get("structured_output")
    if out is None:
        try:
            out = json.loads(d.get("result") or "")
        except (json.JSONDecodeError, TypeError):
            return None
    try:
        return Score.model_validate(out)
    except Exception as e:
        print(f"  claude ({model}) schema mismatch: {e}", file=sys.stderr)
        return None


class ClaudeUnavailable(Exception):
    """Auth expired or usage limit hit: stop scoring this run, keep the flats for later."""


def needs_second_look(s):
    return s is None or s.score >= config.ESCALATE_MIN_SCORE or s.outdoor_space == "unclear"


def score_listing(pd, facts, notes):
    """Returns (Score or None, which model produced it)."""
    with tempfile.TemporaryDirectory(prefix="flat-") as tmp:
        folder = Path(tmp)
        images = download_images(pd, folder)
        parts = [INSTRUCTIONS, "Image files to Read:"]
        parts += [f"- {label}: {path}" for label, path in images]
        parts += [
            "",
            f"Title: {pd.get('text', {}).get('pageTitle', '')}",
            f"Facts: {json.dumps(facts, ensure_ascii=False)}",
            "Key features: " + "; ".join(pd.get("keyFeatures") or []),
            "Description:\n" + strip_html(pd.get("text", {}).get("description", ""))[:6000],
        ]
        if notes:
            parts.append("Notes from the pre-filter: " + " ".join(notes))
        prompt = "\n".join(parts)

        first = ask_claude(config.SCREEN_MODEL, "low", prompt, tmp)
        if not needs_second_look(first):
            return first, config.SCREEN_MODEL
        second = ask_claude(config.JUDGE_MODEL, "medium", prompt, tmp)
        if second:
            return second, config.JUDGE_MODEL
        return first, config.SCREEN_MODEL


# --------------------------------------------------------------- Telegram

class Telegram:
    def __init__(self, dry_run):
        self.token = os.environ.get("TELEGRAM_BOT_TOKEN")
        self.chat = os.environ.get("TELEGRAM_CHAT_ID")
        self.dry = dry_run or not (self.token and self.chat)

    def _api(self, method, body):
        req = urllib.request.Request(
            f"https://api.telegram.org/bot{self.token}/{method}",
            data=json.dumps(body).encode(), headers={"Content-Type": "application/json"})
        for attempt in range(4):
            try:
                with urllib.request.urlopen(req, timeout=30) as r:
                    return json.loads(r.read())
            except urllib.error.HTTPError as e:
                err = json.loads(e.read() or b"{}")
                if e.code == 429:
                    time.sleep(err.get("parameters", {}).get("retry_after", 3) + 1)
                    continue
                raise RuntimeError(f"Telegram {e.code}: {err.get('description')}")
        raise RuntimeError("Telegram rate limited")

    @staticmethod
    def quiet():
        h = dt.datetime.now(LONDON).hour
        return h >= config.QUIET_FROM_HOUR or h < config.QUIET_UNTIL_HOUR

    def text(self, html):
        if self.dry:
            print("---- TELEGRAM (dry run) ----\n" + html)
            return
        self._api("sendMessage", {"chat_id": self.chat, "text": html, "parse_mode": "HTML",
                                  "link_preview_options": {"is_disabled": True},
                                  "disable_notification": self.quiet()})
        time.sleep(1)

    def listing(self, photo, caption, more=""):
        """Photo with a short caption (Telegram caps captions at 1024 chars)."""
        if self.dry:
            print(f"---- TELEGRAM (dry run) photo={photo} ----\n{caption}\n{more}")
            return
        if photo:
            try:
                self._api("sendPhoto", {"chat_id": self.chat, "photo": photo,
                                        "caption": caption[:1024], "parse_mode": "HTML",
                                        "disable_notification": self.quiet()})
            except RuntimeError:
                self.text(caption)
        else:
            self.text(caption)
        if more:
            self.text(more)
        time.sleep(1)


OUTDOOR_LABEL = {
    "private_balcony": "Private balcony", "terrace": "Terrace", "roof_terrace": "Roof terrace",
    "private_garden": "Private garden", "communal_garden": "Communal garden",
    "juliet_balcony_only": "Juliet balcony only", "none_visible": "None seen", "unclear": "Unclear",
}


def esc(s):
    return html_escape(str(s or ""), quote=False)


def listing_message(rec, prefix=""):
    """Returns (caption, follow-up) in Telegram HTML."""
    s = rec.get("ai") or {}
    head = (f"{prefix}<b>{s.get('score', '?')}/10 · £{rec['price']:,} pcm</b>\n"
            f"<a href=\"{rec['url']}\">{esc(rec['address'])}</a>\n"
            f"<i>{esc(s.get('one_liner') or 'Not scored - have a look yourself.')}</i>")
    lines = [head, ""]
    if rec["price"] > config.BUDGET:
        lines.append(f"💷 £{rec['price'] - config.BUDGET:,} over your £{config.BUDGET:,} budget")
    if s:
        study = s.get("second_bedroom", "unclear").replace("_", " ")
        if s.get("second_bedroom_size"):
            study += f" ({esc(s['second_bedroom_size'])})"
        lines.append(f"🌿 {OUTDOOR_LABEL.get(s['outdoor_space'], s['outdoor_space'])}")
        lines.append(f"☀️ Light: {esc(s.get('light', '?'))} · ceilings: {esc(s.get('ceilings', '?'))} · "
                     f"living room: {esc(s.get('living_room', '?').replace('_', ' '))}")
        lines.append(f"🖥 Study / 2nd bed: {study}")
        lines.append(f"🏠 {esc(s.get('furnished'))} · floor {esc(s.get('floor'))} · lift {esc(s.get('lift'))}")
    lines.append(f"📅 Available {esc(rec.get('available') or 'not stated')}")
    if s.get("highlights"):
        lines.append("✅ " + esc(" · ".join(s["highlights"][:3])))
    if s.get("concerns"):
        lines.append("⚠️ " + esc(" · ".join(s["concerns"][:3])))
    for n in rec.get("notes") or []:
        lines.append("ℹ️ " + esc(n))
    lines += ["", commute_block(rec)]
    full = "\n".join(lines)
    if len(full) <= 1024:
        return full, ""
    # Too long for a photo caption: keep the headline on the photo, details below.
    return "\n".join(lines[:1]), "\n".join(lines[2:])


def commute_block(rec):
    out = []
    for who, key in ((config.HIS_NAME, "his"), (config.HER_NAME, "her")):
        mins = rec.get(f"{key}_min")
        route = rec.get(f"{key}_route") or "no route found"
        out.append(f"🚇 <b>{who}: {mins or '?'} min</b>\n   {esc(route)}")
    if rec.get("hockey"):
        out.append("🏑 Hockey: " + " · ".join(f"{esc(n)} {m or '?'} min" for n, m in rec["hockey"]))
    return "\n".join(out)


def is_instant(rec):
    s = rec.get("ai")
    if not s or s["outdoor_space"] not in config.OUTDOOR_OK:
        return False
    stretch = rec["price"] > config.BUDGET or (rec.get("her_min") or 0) > config.HER_MAX_MINUTES
    need = config.OVER_BUDGET_MIN_SCORE if stretch else config.INSTANT_MIN_SCORE
    return s["score"] >= need


# ------------------------------------------------------------------- State

def load_state():
    if STATE_PATH.exists():
        return json.loads(STATE_PATH.read_text())
    return {"seen": {}, "digest": [], "filtered": [], "last_digest": None, "spend_usd": 0.0}


def save_state(st):
    STATE_PATH.parent.mkdir(exist_ok=True)
    tmp = STATE_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(st, indent=1, ensure_ascii=False))
    tmp.replace(STATE_PATH)


# -------------------------------------------------------------------- Main

def last_touched(p):
    stamps = [p.get("firstVisibleDate"), (p.get("listingUpdate") or {}).get("listingUpdateDate")]
    return max(dt.datetime.fromisoformat(x.replace("Z", "+00:00")) for x in stamps if x)


def collect_new(st, backfill_days):
    """Walk newest-first until a whole page is already known (or too old)."""
    first_run = not st["seen"]
    cutoff = None
    if backfill_days is not None:
        cutoff = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=backfill_days)
    fresh, reduced = [], []
    for page in range(MAX_PAGES):
        props, total = search_page(page * 24)
        if page == 0:
            print(f"Search has {total} live listings")
        if not props:
            break
        page_had_new = False
        for p in props:
            pid = str(p["id"])
            first_vis = dt.datetime.fromisoformat(p["firstVisibleDate"].replace("Z", "+00:00"))
            known = st["seen"].get(pid)
            if known and cutoff and known.get("route") == "baseline":
                known = None  # --backfill re-scores listings the first run only recorded
            if known:
                if monthly(p) < known.get("price", 10**9):
                    reduced.append(p)
                    page_had_new = True
                continue
            if cutoff and first_vis < cutoff:
                continue
            if first_run and cutoff is None:
                # First ever run without --backfill: remember, don't flood.
                st["seen"][pid] = {"price": monthly(p), "route": "baseline"}
                page_had_new = True
                continue
            fresh.append(p)
            page_had_new = True
        if cutoff:
            # Backfill: page past already-scored flats; stop once a whole page was
            # last touched (listed, reduced, relisted) before the window.
            if all(last_touched(p) < cutoff for p in props if not p.get("featuredProperty")):
                break
        elif not page_had_new:
            break
        time.sleep(POLITE_DELAY)
    # de-dup (featured listings repeat on every page)
    seen_ids, out = set(), []
    for p in fresh:
        if str(p["id"]) not in seen_ids:
            seen_ids.add(str(p["id"]))
            out.append(p)
    return out, reduced


def process(p, st, tg, force_send=False):
    pid = str(p["id"])
    url = f"https://www.rightmove.co.uk/properties/{pid}"
    pd = listing_detail(pid)
    time.sleep(POLITE_DELAY)
    text = " ".join([pd.get("text", {}).get("pageTitle", "") or "",
                     strip_html(pd.get("text", {}).get("description", "")),
                     " ".join(pd.get("keyFeatures") or [])])
    loc = pd.get("location") or p.get("location") or {}
    lat, lon = loc.get("latitude"), loc.get("longitude")
    pin_exact = (loc.get("pinType") or "") == "ACCURATE_POINT"
    his_min, his_legs = his_commute(lat, lon) if lat else (None, [])
    her_min, her_legs = her_commute(lat, lon) if lat else (None, [])
    hockey = hockey_commutes(lat, lon) if lat else []
    his_route, her_route = legs_text(his_legs, his_min), legs_text(her_legs, her_min)
    lettings = pd.get("lettings") or {}

    rec = {
        "id": pid, "url": url, "price": monthly(p),
        "address": (p.get("displayAddress") or pd.get("address", {}).get("displayAddress") or "")
                   .replace("\n", " ").replace(" ,", ","),
        "listed": p.get("firstVisibleDate", ""), "available": lettings.get("letAvailableDate"),
        "his_min": his_min, "his_route": his_route, "her_min": her_min, "her_route": her_route,
        "hockey": hockey,
        "photo": ((pd.get("images") or [{}])[0].get("resizedImageUrls") or {}).get("size656x437"),
    }
    reason, notes = hard_filter(text, lettings, his_min, pin_exact)
    if his_min and config.HIS_MAX_MINUTES < his_min <= config.HIS_HARD_LIMIT_MINUTES:
        notes.append(f"{config.HIS_NAME} commute {his_min} min - just over the {config.HIS_MAX_MINUTES}.")
    if her_min and her_min > config.HER_MAX_MINUTES:
        notes.append(f"{config.HER_NAME} commute {her_min} min - over her {config.HER_MAX_MINUTES}.")
    if his_min and his_min > config.HIS_HARD_LIMIT_MINUTES and not pin_exact:
        notes.append(f"TfL says {his_min} min for {config.HIS_NAME}, but the map pin is approximate.")
    rec["notes"] = notes

    if reason:
        rec["route"] = "filtered"
        st["filtered"].append({"id": pid, "url": url, "reason": reason, "address": rec["address"],
                               "price": rec["price"]})
        print(f"  ✗ {pid} {rec['address']}: {reason}")
    else:
        facts = {
            "rent_pcm": rec["price"], "address": rec["address"],
            "furnish_type": lettings.get("furnishType"), "available": rec["available"],
            "deposit": lettings.get("deposit"), "min_term_months": lettings.get("minimumTermInMonths"),
            "size": (pd.get("sizings") or [])[:2],
            "nearest_stations": [f"{s['name']} {s['distance']:.2f} mi" for s in (pd.get("nearestStations") or [])],
            f"{config.HIS_NAME}_commute_min": his_min, f"{config.HIS_NAME}_route": his_route,
            f"{config.HER_NAME}_commute_min": her_min, f"{config.HER_NAME}_route": her_route,
            "budget_pcm": config.BUDGET, f"{config.HER_NAME}_max_commute_min": config.HER_MAX_MINUTES,
        }
        ai, model = score_listing(pd, facts, notes)  # ClaudeUnavailable propagates: flat stays unseen
        rec["ai"] = ai.model_dump() if ai else None
        rec["model"] = model if ai else None
        if force_send:
            rec["route"] = "digest"
            verdict = "would be sent instantly" if is_instant(rec) else "would go to the 6pm digest"
            rec["notes"].append(f"Manual check: the bot {verdict}.")
            tg.listing(rec["photo"], *listing_message(rec, prefix="🔎 Manual check · "))
            print(f"  ? {pid} {rec['address']}: {ai.score if ai else '?'}/10 ({model}) - {verdict}")
        elif is_instant(rec):
            rec["route"] = "instant"
            tg.listing(rec["photo"], *listing_message(rec))
            print(f"  ★ {pid} {rec['address']}: {ai.score}/10 ({model}) -> sent now")
        else:
            rec["route"] = "digest"
            st["digest"].append(rec)
            print(f"  · {pid} {rec['address']}: {ai.score if ai else '?'}/10 ({model}) -> digest")

    st["seen"][pid] = {"price": rec["price"], "route": rec["route"],
                       "score": (rec.get("ai") or {}).get("score"), "address": rec["address"],
                       "first_seen": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
                       "ai": rec.get("ai"), "his_min": his_min, "her_min": her_min,
                       "his_route": his_route, "her_route": her_route, "hockey": hockey, "available": rec["available"], "photo": rec["photo"]}


def handle_reduction(p, st, tg):
    pid = str(p["id"])
    known = st["seen"][pid]
    old, new = known["price"], monthly(p)
    known["price"] = new
    if known.get("route") in ("filtered", "baseline"):
        return
    rec = {"id": pid, "url": f"https://www.rightmove.co.uk/properties/{pid}", "price": new,
           "address": known.get("address", ""), "ai": known.get("ai"), "his_min": known.get("his_min"),
           "her_min": known.get("her_min"), "her_route": known.get("her_route"),
           "his_route": known.get("his_route"), "hockey": known.get("hockey"),
           "available": known.get("available"), "photo": known.get("photo"),
           "notes": [f"Price dropped £{old:,} → £{new:,}."]}
    if (known.get("score") or 0) >= config.INSTANT_MIN_SCORE - 1:
        tg.listing(rec["photo"], *listing_message(rec, prefix="📉 Price drop · "))
    else:
        st["digest"].append(rec)


def send_digest(st, tg):
    today = dt.datetime.now(LONDON).date().isoformat()
    score = lambda r: (r.get("ai") or {}).get("score")
    # Near misses only: decent scores that didn't ping (no confirmed outdoor space or
    # over budget), plus anything the AI couldn't score - never hide those.
    shown = [r for r in st["digest"] if score(r) is None or score(r) >= config.DIGEST_MIN_SCORE]
    shown.sort(key=lambda r: -(score(r) or 99))
    shown = shown[:config.DIGEST_MAX_ITEMS]
    hidden = len(st["digest"]) - len(shown)
    filtered = st["filtered"]

    lines = [f"<b>🗂 Near misses · {today}</b>"]
    for r in shown:
        s = r.get("ai") or {}
        out = OUTDOOR_LABEL.get(s.get("outdoor_space", ""), "?")
        why = ("over budget" if r["price"] > config.BUDGET
               else "outdoor space not confirmed" if s.get("outdoor_space") not in config.OUTDOOR_OK
               else "")
        lines.append(f"<b>{score(r) if score(r) is not None else 'unscored'}/10</b> £{r['price']:,} · "
                     f"<a href=\"{r['url']}\">{esc(r['address'][:40])}</a> · 🌿 {out}"
                     + (f" · <i>{why}</i>" if why else ""))
    if not shown:
        lines.append("Nothing close today.")
    tail = []
    if hidden:
        tail.append(f"{hidden} lower-scored flat(s) not shown")
    if filtered:
        tail.append(f"{len(filtered)} removed by filters: " + "; ".join(
            f"<a href=\"{f['url']}\">{esc(f['address'][:30])}</a> ({esc(f['reason'].split(' (')[0])})"
            for f in filtered[:5]) + (" …" if len(filtered) > 5 else ""))
    if tail:
        lines += ["", "\n".join(tail)]
    tg.text("\n".join(lines)[:4000])
    st["digest"], st["filtered"], st["last_digest"] = [], [], today


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--backfill", type=float, default=None, help="days of listings to treat as new")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--digest-now", action="store_true")
    ap.add_argument("--limit", type=int, default=None, help="process at most N listings (testing)")
    ap.add_argument("--only", default=None, help="score these listing ids (comma-separated) and post them")
    args = ap.parse_args()

    st = load_state()
    tg = Telegram(args.dry_run)

    if args.only:
        for pid in args.only.split(","):
            pid = pid.strip()
            card = {"id": pid, "price": {"amount": int(listing_detail(pid)["prices"]["primaryPrice"]
                                                    .replace("£", "").replace(",", "").split()[0]),
                                         "frequency": "monthly"},
                    "displayAddress": "", "firstVisibleDate": ""}
            st["seen"].pop(pid, None)
            process(card, st, tg, force_send=True)
        save_state(st)
        return
    fresh, reduced = ([], []) if args.digest_now else collect_new(st, args.backfill)
    cap = args.limit or (400 if args.backfill else MAX_NEW_PER_RUN)
    if len(fresh) > cap:
        print(f"Capping {len(fresh)} new listings to {cap} this run (rest picked up next run)")
        fresh = fresh[:cap]
    print(f"{len(fresh)} new listing(s), {len(reduced)} price drop(s)")
    for p in fresh:
        try:
            process(p, st, tg)
        except ClaudeUnavailable as e:
            print(f"Claude unavailable, stopping; unscored flats retry next run: {e}", file=sys.stderr)
            today = dt.datetime.now(LONDON).date().isoformat()
            if st.get("claude_warned") != today:
                tg.text(f"⚠️ Flat hunter can't reach Claude ({esc(str(e)[:150])}). New flats are "
                        f"queued and will be scored when it's back. If this says 'authenticate', "
                        f"the CLAUDE_CODE_OAUTH_TOKEN secret needs renewing.")
                st["claude_warned"] = today
            break
        except Exception as e:
            print(f"  ! {p['id']} failed: {e}", file=sys.stderr)
        save_state(st)  # survive a crash mid-run
    for p in reduced:
        handle_reduction(p, st, tg)

    now = dt.datetime.now(LONDON)
    if args.digest_now or (now.hour >= config.DIGEST_HOUR_LONDON and st.get("last_digest") != now.date().isoformat()
                           and (st["digest"] or st["filtered"])):
        send_digest(st, tg)
    save_state(st)


if __name__ == "__main__":
    main()
