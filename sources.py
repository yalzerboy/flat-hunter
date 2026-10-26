"""Listing sources. Each one turns a site's search and listing pages into the same shape:

  card    (from the search page, cheap):  key, source, id, url, price, beds, lat, lon,
                                          listed (datetime or None), address, featured
  detail  (one listing page):             title, description, features, facts, lettings,
                                          floorplans, photos, photo, stations, sizings,
                                          price, address, lat, lon, pin_exact

Rightmove keys are the bare listing id (what state has always used); the others are
prefixed ("zp:123", "otm:123", "or:123").

Zoopla and OpenRent refuse data-centre IPs (GitHub's runners get 403/405 whatever the
browser fingerprint), so on Actions their pages go through PROXY_URL, a residential
proxy. Without it those two are skipped there; from a home connection they work direct.
Photos come from CDNs that don't block, so they never use the proxy.
"""

import datetime as dt
import json
import math
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from html import unescape

import config

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/129.0 Safari/537.36")
POLITE_DELAY = 1.5      # seconds between page requests to one site
ON_ACTIONS = os.environ.get("GITHUB_ACTIONS") == "true"

# The search area and limits (config.SEARCH): OpenRent is filtered against them, and
# every site's results are kept to the circle, as Rightmove's radius search does.
SEARCH = getattr(config, "SEARCH", None)


# --------------------------------------------------------------------- HTTP

def get(url, headers=None, binary=False, tries=3):
    """Plain urllib: Rightmove and every image CDN."""
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


_browser = None


def browser_get(url, proxied=False, tries=3):
    """A request that looks like Safari (Zoopla's Cloudflare rejects urllib and Chrome's
    TLS fingerprint), optionally through the residential proxy."""
    global _browser
    from curl_cffi import requests as cf
    if _browser is None:
        _browser = cf.Session(impersonate="safari")
    proxy = os.environ.get("PROXY_URL") if proxied else None
    kw = {"proxies": {"http": proxy, "https": proxy}} if proxy else {}
    for attempt in range(tries):
        try:
            r = _browser.get(url, timeout=40, headers={"Accept-Language": "en-GB,en;q=0.9"}, **kw)
            if r.status_code in (404, 410):
                raise LookupError(f"{r.status_code} {url}")
            if r.status_code == 200:
                return r.text
            err = RuntimeError(f"HTTP {r.status_code} from {urllib.parse.urlsplit(url).netloc}")
        except LookupError:
            raise
        except Exception as e:  # curl errors, proxy hiccups
            err = e
        if attempt == tries - 1:
            raise err
        # A 429 means slow down properly; anything else is worth a quick retry.
        time.sleep((30 if "429" in str(err) else 4) * (attempt + 1))


def km(lat1, lon1, lat2, lon2):
    dx = (lon2 - lon1) * 69.0 * math.cos(math.radians(lat1)) * 1.609
    dy = (lat2 - lat1) * 69.0 * 1.609
    return math.hypot(dx, dy)


def in_search_area(card):
    if card.get("lat") is None or not SEARCH:
        return True  # can't tell: keep it
    return km(*SEARCH["centre"], card["lat"], card["lon"]) <= SEARCH["radius_miles"] * 1.609 + 0.2


def strip_html(s):
    s = re.sub(r"<br\s*/?>|</p>|</li>", "\n", s or "")
    s = re.sub(r"<[^>]+>", "", s)
    return re.sub(r"\n{3,}", "\n\n", unescape(s)).strip()


def pounds(s):
    m = re.search(r"£\s*([\d,]+(?:\.\d+)?)", s or "")
    return float(m.group(1).replace(",", "")) if m else None


def utc(ts):
    return dt.datetime.fromisoformat(ts.replace("Z", "+00:00")) if ts else None


class Source:
    name = label = ""
    blocks_datacentres = False  # needs PROXY_URL on GitHub's runners
    delay = POLITE_DELAY

    config_url = None  # the config.py setting with this site's search URL

    def available(self):
        """(ok, reason when not)."""
        if self.config_url and not (getattr(config, self.config_url, None) and SEARCH):
            return False, f"no {self.config_url} / SEARCH in config.py"
        if self.blocks_datacentres and ON_ACTIONS and not os.environ.get("PROXY_URL"):
            return False, "blocks GitHub's servers and PROXY_URL isn't set"
        return True, ""

    @property
    def proxied(self):
        return self.blocks_datacentres and ON_ACTIONS

    def key(self, native_id):
        return f"{self.name}:{native_id}"


# --------------------------------------------------------------- Rightmove

class Rightmove(Source):
    name, label = "rm", "Rightmove"

    def key(self, native_id):
        return str(native_id)

    def pages(self, max_pages):
        for page in range(max_pages):
            html = get(config.SEARCH_URL + f"&index={page * 24}")
            m = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', html, re.S)
            if not m:
                raise RuntimeError("Rightmove search page changed shape (no __NEXT_DATA__)")
            sr = json.loads(m.group(1))["props"]["pageProps"]["searchResults"]
            if page == 0:
                print(f"Rightmove: {sr['resultCount']} live listings")
            if not sr["properties"]:
                return
            yield [self.card(p) for p in sr["properties"]]
            time.sleep(self.delay)

    def card(self, p):
        loc = p.get("location") or {}
        stamps = [utc(p.get("firstVisibleDate")), utc((p.get("listingUpdate") or {}).get("listingUpdateDate"))]
        return {"key": str(p["id"]), "source": self.name, "id": str(p["id"]),
                "url": f"https://www.rightmove.co.uk/properties/{p['id']}",
                "price": rightmove_monthly(p["price"]["amount"], p["price"].get("frequency")),
                "beds": p.get("bedrooms"), "lat": loc.get("latitude"), "lon": loc.get("longitude"),
                "listed": stamps[0], "touched": max((s for s in stamps if s), default=None),
                "address": (p.get("displayAddress") or "").replace("\n", " ").replace(" ,", ","),
                "featured": bool(p.get("featuredProperty"))}

    def card_for_id(self, native_id):
        return {"key": str(native_id), "source": self.name, "id": str(native_id),
                "url": f"https://www.rightmove.co.uk/properties/{native_id}"}

    def detail(self, card):
        pd = rightmove_page_model(get(card["url"]))
        if not pd or "propertyData" not in pd:
            raise RuntimeError(f"listing {card['id']}: no page model")
        pd = pd["propertyData"]
        loc = pd.get("location") or {}
        images = pd.get("images") or []
        price = card.get("price")
        if price is None:
            price = int(pounds(pd["prices"]["primaryPrice"]))
        return {
            "title": (pd.get("text") or {}).get("pageTitle", ""),
            "description": strip_html((pd.get("text") or {}).get("description", "")),
            "features": pd.get("keyFeatures") or [], "facts": [],
            "lettings": pd.get("lettings") or {},
            "floorplans": [fp["url"] for fp in (pd.get("floorplans") or [])[:3]
                           if fp.get("type", "IMAGE") == "IMAGE" and fp.get("url")],
            "photos": [(im.get("resizedImageUrls") or {}).get(config.PHOTO_SIZE) or im.get("url")
                       for im in images],
            "photo": ((images or [{}])[0].get("resizedImageUrls") or {}).get("size656x437"),
            "stations": [f"{s['name']} {s['distance']:.2f} mi" for s in (pd.get("nearestStations") or [])],
            "sizings": (pd.get("sizings") or [])[:2],
            "price": price,
            "address": card.get("address") or (pd.get("address") or {}).get("displayAddress", ""),
            "lat": loc.get("latitude"), "lon": loc.get("longitude"),
            "pin_exact": (loc.get("pinType") or "") == "ACCURATE_POINT",
        }


def rightmove_monthly(amount, freq):
    freq = (freq or "monthly").lower()
    if freq == "weekly":
        return round(amount * 52 / 12)
    if freq == "daily":
        return round(amount * 365 / 12)
    return amount


def rightmove_page_model(html):
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


# ------------------------------------------------------------ OnTheMarket

class OnTheMarket(Source):
    name, label = "otm", "OnTheMarket"
    config_url = "OTM_SEARCH_URL"

    def search_url(self):
        return config.OTM_SEARCH_URL

    def pages(self, max_pages):
        for page in range(1, max_pages + 1):
            html = browser_get(self.search_url() + (f"&page={page}" if page > 1 else ""))
            results = next_data(html)["props"]["pageProps"]["initialReduxState"]["results"]
            if page == 1:
                print(f"OnTheMarket: {results.get('totalResults')} live listings")
            if not results["list"]:
                return
            yield [self.card(p) for p in results["list"]]
            if page >= ((results.get("paginationControls") or {}).get("lastPage") or 99):
                return
            time.sleep(self.delay)

    def card(self, p):
        loc = p.get("location") or {}
        added = (p.get("days-since-added-reduced") or "").lower()
        now = dt.datetime.now(dt.timezone.utc)
        listed = now if "added today" in added else now - dt.timedelta(days=1) if "added yesterday" in added else None
        price = p.get("price") or ""
        pcm = pounds(price.split("(")[0]) if "pcm" in price.split("(")[0] else (
            round(pounds(price) * 52 / 12) if pounds(price) else None)
        return {"key": self.key(p["id"]), "source": self.name, "id": str(p["id"]),
                "url": "https://www.onthemarket.com" + p["details-url"],
                "price": round(pcm) if pcm else None, "beds": p.get("bedrooms"),
                "lat": loc.get("lat"), "lon": loc.get("lon"), "listed": listed, "touched": listed,
                "address": p.get("address") or "", "featured": bool(p.get("spotlight?"))}

    def card_for_id(self, native_id):
        return {"key": self.key(native_id), "source": self.name, "id": str(native_id),
                "url": f"https://www.onthemarket.com/details/{native_id}/"}

    def detail(self, card):
        p = next_data(browser_get(card["url"]))["props"]["pageProps"]["initialReduxState"]["property"]
        letting = (p.get("lettingDetails") or {}).get("items") or []
        let_type = next((x for x in letting if "term let" in x.lower()), "")
        avail = next((x.split(":", 1)[1].strip() for x in letting if x.lower().startswith("availability")), None)
        loc = p.get("location") or {}
        return {
            "title": p.get("propertyTitle") or "",
            "description": strip_html((p.get("summary") or "") + "\n\n" + (p.get("description") or "")),
            "features": [f.get("feature") for f in (p.get("features") or []) if f.get("feature")],
            "facts": letting + [f"{k['title']}: {k['value']}" for k in (p.get("keyInfo") or [])
                                if k.get("value") and not k.get("isUnknown")],
            "lettings": {"letType": let_type, "letAvailableDate": avail},
            "floorplans": [f.get("largeUrl") or f.get("url") for f in (p.get("floorplans") or [])[:3]
                           if f.get("isImage", True)],
            "photos": [im.get("largeUrl") or im.get("url") for im in (p.get("images") or [])],
            "photo": ((p.get("images") or [{}])[0]).get("largeUrl"),
            "stations": [f"{s.get('name')} {s.get('displayDistance', '')}".strip() for s in (p.get("station") or [])],
            "sizings": [],
            "price": p.get("priceRaw") or card.get("price"),
            "address": p.get("displayAddress") or card.get("address", ""),
            "lat": loc.get("lat", card.get("lat")), "lon": loc.get("lon", card.get("lon")),
            "pin_exact": False,  # OnTheMarket doesn't say; never hard-filter on its pin
        }


def next_data(html):
    m = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', html, re.S)
    if not m:
        raise RuntimeError("page changed shape (no __NEXT_DATA__)")
    return json.loads(m.group(1))


# ------------------------------------------------------------------ Zoopla
#
# Zoopla's pages are React Server Components: the data is a stream of
# self.__next_f.push([1, "..."]) chunks. Long strings live in their own
# "NN:T<hex length>," text chunks and are referenced as "$NN".

class Zoopla(Source):
    name, label = "zp", "Zoopla"
    blocks_datacentres = True
    delay = 4  # it answers bursts with 429
    config_url = "ZOOPLA_SEARCH_URL"

    def search_url(self):
        return config.ZOOPLA_SEARCH_URL

    def pages(self, max_pages):
        for page in range(1, max_pages + 1):
            rsc = rsc_text(browser_get(self.search_url() + f"&pn={page}", proxied=self.proxied))
            regular = rsc_array(rsc, "regularListingsFormatted")
            if regular is None:
                raise RuntimeError("Zoopla search page changed shape (no regularListingsFormatted)")
            if page == 1:
                m = re.search(r'"totalResults":(\d+)', rsc) or re.search(r'"resultCount":(\d+)', rsc)
                print(f"Zoopla: {m.group(1) if m else '?'} live listings")
            featured = rsc_array(rsc, "featuredListingsFormatted") or []
            cards = [self.card(p) for p in regular + featured if isinstance(p, dict) and p.get("listingId")]
            if not regular:
                return
            yield cards
            time.sleep(self.delay)

    def card(self, p):
        pos = p.get("pos") if isinstance(p.get("pos"), dict) else {}
        feats = p.get("features") if isinstance(p.get("features"), list) else []
        beds = next((f.get("content") for f in feats if f.get("iconId") == "bed"), None)
        raw, alt = p.get("priceUnformatted"), p.get("alternativeRentFrequencyLabel") or ""
        weekly = "pw" in (p.get("price") or "") or "pcm" in alt or (raw is not None and raw < 1200)
        try:
            listed = dt.datetime.strptime(re.sub(r"(\d)(st|nd|rd|th)", r"\1", p.get("publishedOn") or ""),
                                          "%d %b %Y").replace(tzinfo=dt.timezone.utc)
        except ValueError:
            listed = None
        return {"key": self.key(p["listingId"]), "source": self.name, "id": str(p["listingId"]),
                "url": f"https://www.zoopla.co.uk/to-rent/details/{p['listingId']}/",
                "price": (round(raw * 52 / 12) if weekly else round(raw)) if raw else None,
                "beds": beds, "lat": pos.get("lat"), "lon": pos.get("lng"),
                "listed": listed, "touched": listed, "address": p.get("address") or "",
                "featured": p.get("listingType") == "featured"}

    def card_for_id(self, native_id):
        return {"key": self.key(native_id), "source": self.name, "id": str(native_id),
                "url": f"https://www.zoopla.co.uk/to-rent/details/{native_id}/"}

    def detail(self, card):
        html = browser_get(card["url"], proxied=self.proxied)
        rsc = rsc_text(html)
        m = re.search(r'\{"__typename":"ListingData"', rsc)
        if not m:
            raise RuntimeError(f"Zoopla listing {card['id']}: no ListingData")
        d, _ = json.JSONDecoder().raw_decode(rsc, m.start())
        chunks = rsc_chunks(rsc)
        resolve = lambda v: chunks.get(v[1:], "") if isinstance(v, str) and v.startswith("$") else (v or "")
        img = "https://lid.zoocdn.com/u/1024/768/{}".format
        coords = (d.get("location") or {}).get("coordinates") or {}
        pricing = d.get("pricing") or {}
        price = pricing.get("internalValue")
        if price and pricing.get("rentFrequencyLabel") == "pw":
            price = round(price * 52 / 12)
        facts = [f"{n['title']}: {n['value']}" for n in (d.get("ntsInfo") or [])
                 if isinstance(n, dict) and n.get("value") not in (None, "", "Ask agent", "Not available")]
        if (d.get("floorArea") or {}).get("label"):
            facts.append(f"Floor area: {d['floorArea']['label']}")
        title = re.search(r"<title>([^<]*)", html)
        avail = re.search(r'"availableFrom(?:Date)?":"([^"$][^"]*)"', rsc)
        photos = [img(i["filename"]) for i in (d.get("propertyImage") or []) if i.get("filename")]
        return {
            "title": unescape(title.group(1)).replace(" - Zoopla", "") if title else d.get("title", ""),
            "description": strip_html(resolve(d.get("detailedDescription"))),
            "features": ((d.get("features") or {}).get("bullets") or []) if isinstance(d.get("features"), dict) else [],
            "facts": facts,
            "lettings": {"letAvailableDate": avail.group(1) if avail else None},
            "floorplans": [img(f["filename"]) for f in ((d.get("floorPlan") or {}).get("image") or [])[:3]
                           if f.get("filename")],
            "photos": photos,
            "photo": photos[0].replace("/u/1024/768/", "/645/430/") if photos else None,
            "stations": [], "sizings": [],
            "price": price or card.get("price"),
            "address": d.get("displayAddress") or card.get("address", ""),
            "lat": coords.get("latitude", card.get("lat")), "lon": coords.get("longitude", card.get("lon")),
            "pin_exact": False,
        }


def rsc_text(html):
    parts = re.findall(r'self\.__next_f\.push\(\[1,"(.*?)"\]\)</script>', html, re.S)
    return "".join(json.loads(f'"{p}"') for p in parts)


def rsc_array(rsc, key):
    i = rsc.find(f'"{key}":[')
    if i < 0:
        return None
    arr, _ = json.JSONDecoder().raw_decode(rsc, i + len(key) + 3)
    return arr


def rsc_chunks(rsc):
    """Text chunks: 'NN:T<hex byte length>,<text>'."""
    out, raw = {}, rsc.encode()
    for m in re.finditer(rb"(?:^|\n)([0-9a-f]+):T([0-9a-f]+),", raw):
        start = m.end()
        out[m.group(1).decode()] = raw[start:start + int(m.group(2), 16)].decode("utf-8", "replace")
    return out


# ---------------------------------------------------------------- OpenRent
#
# Private landlords, so these mostly aren't on Rightmove at all. One search
# page carries every listing in the area as parallel JS arrays (no paging);
# the filters are applied here.

class OpenRent(Source):
    name, label = "or", "OpenRent"
    blocks_datacentres = True
    config_url = "OPENRENT_SEARCH_URL"

    def search_url(self):
        return config.OPENRENT_SEARCH_URL

    def pages(self, max_pages):
        html = browser_get(self.search_url(), proxied=self.proxied)

        def arr(name, cast=str):
            m = re.search(r"var " + name + r" ?= ?\[(.*?)\];", html, re.S)
            if not m:
                raise RuntimeError(f"OpenRent search page changed shape (no {name})")
            return [cast(x.strip().strip("'\"")) if x.strip() not in ("", "null") else None
                    for x in m.group(1).split(",")]

        ids, prices, beds = arr("PROPERTYIDS"), arr("prices", float), arr("bedrooms", int)
        lats, lons = arr("PROPERTYLISTLATITUDES", float), arr("PROPERTYLISTLONGITUDES", float)
        shared, live, first = arr("isshared"), arr("islivelistBool"), arr("dateFirstListedMs", int)
        cards = []
        for i, pid in enumerate(ids):
            if shared[i] == "1" or live[i] != "1":
                continue
            if not (SEARCH["min_price"] <= (prices[i] or 0) <= SEARCH["max_price"]):
                continue
            if not (SEARCH["min_beds"] <= (beds[i] or 0) <= SEARCH.get("max_beds", 99)):
                continue
            listed = dt.datetime.fromtimestamp(first[i] / 1000, dt.timezone.utc) if first[i] else None
            cards.append({"key": self.key(pid), "source": self.name, "id": pid,
                          "url": f"https://www.openrent.co.uk/{pid}", "price": round(prices[i]),
                          "beds": beds[i], "lat": lats[i], "lon": lons[i], "listed": listed,
                          "touched": listed, "address": "", "featured": False})
        cards.sort(key=lambda c: c["listed"] or dt.datetime.min.replace(tzinfo=dt.timezone.utc), reverse=True)
        print(f"OpenRent: {len(cards)} matching listings (of {len(ids)} on the page)")
        # Hand them over in pages of 24 so the stop-when-a-page-is-known rule works the same.
        for i in range(0, len(cards), 24):
            yield cards[i:i + 24]

    def card_for_id(self, native_id):
        return {"key": self.key(native_id), "source": self.name, "id": str(native_id),
                "url": f"https://www.openrent.co.uk/{native_id}"}

    def detail(self, card):
        html = browser_get(card["url"], proxied=self.proxied)
        body = re.sub(r"<(script|style)[^>]*>.*?</\1>", "", html, flags=re.S)
        title = unescape(re.search(r"<title>([^<]*)", html).group(1))
        # The listing text sits between the bedrooms/tenants header and "Read more".
        text = strip_html(re.sub(r"<[^>]+>", "\n", body)).replace("\r", "")
        text = re.sub(r"\n\s*\n+", "\n", re.sub(r"[ \t]+", " ", text))
        m = re.search(r"tenants max\.\s*(.*?)\s*(?:Read more|Price & Bills)", text, re.S)
        description = m.group(1).strip() if m else text[:6000]  # shape changed: let the AI read it all
        facts = []
        for label, value in re.findall(r"<tr>\s*<td[^>]*>(.*?)</td>\s*<td[^>]*>(.*?)</td>\s*</tr>", body, re.S):
            label = strip_html(label).strip()
            if "<svg" in value:
                value = "yes" if "text-success" in value else "no" if "text-danger" in value else "?"
            else:
                value = re.sub(r"\s+", " ", strip_html(value)).strip()
            if label and value and len(label) < 60:
                facts.append(f"{label}: {value}")
        photos = list(dict.fromkeys(
            "https://" + u for u in re.findall(r"//(imagescdn\.openrent\.co\.uk/listings/[^\"' ?]+\.(?:jpe?g|png|webp))",
                                               html, re.I)))
        lat = re.search(r'data-lat="([-\d.]+)"', html)
        lon = re.search(r'data-lng="([-\d.]+)"', html)
        price = re.search(r"Rent PCM\s*£([\d,]+(?:\.\d+)?)", text)
        avail = re.search(r"Available From\s*(.+)", text)
        minterm = re.search(r"Minimum Tenancy.{0,600}?(\d+) Months?", text, re.S)
        address = re.sub(r"\s*-\s*To Rent.*$", "", title).split(" - ", 1)[-1]
        return {
            "title": title, "description": description[:8000], "features": [], "facts": facts,
            "lettings": {"letAvailableDate": avail.group(1).strip() if avail else None,
                         "minimumTermInMonths": int(minterm.group(1)) if minterm else None},
            "floorplans": [], "photos": photos,
            "photo": photos[0] if photos else None,
            "stations": [], "sizings": [],
            "price": round(float(price.group(1).replace(",", ""))) if price else card.get("price"),
            "address": address or card.get("address", ""),
            "lat": float(lat.group(1)) if lat else card.get("lat"),
            "lon": float(lon.group(1)) if lon else card.get("lon"),
            "pin_exact": False,  # OpenRent shows an approximate pin
        }


ALL = [Rightmove(), OnTheMarket(), Zoopla(), OpenRent()]
BY_NAME = {s.name: s for s in ALL}


def enabled():
    names = getattr(config, "SOURCES", None)
    return [s for s in ALL if names is None or s.name in names]


def source_of(key):
    return BY_NAME[key.split(":", 1)[0]] if ":" in key else BY_NAME["rm"]

