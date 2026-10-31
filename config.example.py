"""Everything you might want to tweak lives here."""

# Your Rightmove search: paste the URL of a saved search, keep sortType=6 (newest first).
# sortType=6 = newest first, which is what lets each run stop early.
SEARCH_URL = (
    "https://www.rightmove.co.uk/property-to-rent/find.html"
    "?locationIdentifier=STATION%5E0000&sortType=6&minBedrooms=2&maxPrice=3000&radius=3"
)

# The same search on the other sites: run it on each site and paste the URL.
# Zoopla: sort by newest (results_sort=newest_listings). OnTheMarket: sort-field=update_date.
# A site with no URL here is skipped.
OTM_SEARCH_URL = ("https://www.onthemarket.com/to-rent/2-bed-property/some-area/"
                  "?min-price=2000&max-price=3000&radius=3.0&sort-field=update_date")
ZOOPLA_SEARCH_URL = ("https://www.zoopla.co.uk/to-rent/property/station/rail/some-station/"
                     "?beds_min=2&price_frequency=per_month&price_min=2000&price_max=3000&radius=3"
                     "&results_sort=newest_listings&search_source=to-rent")
OPENRENT_SEARCH_URL = ("https://www.openrent.co.uk/properties-to-rent/some-area?term=Some%20Area"
                       "&prices_min=2000&prices_max=3000&bedrooms_min=2&within=5")
# The search circle and limits. OpenRent's page lists everything nearby, so these are
# applied here; every other site's results are also kept to the circle.
SEARCH = {"centre": (51.5000, -0.1200), "radius_miles": 3, "min_price": 2000,
          "max_price": 3000, "min_beds": 2, "max_beds": 2}
# Optional: more circles outside the main one, e.g.
#   "extra_areas": [{"name": "little-venice", "centre": (51.526, -0.188), "radius_miles": 0.8}]
# A new area gets its own first run on each site: recent flats scored, older ones recorded.
# The site URLs must reach it (their radius counts from their own centre).
# Optional edges inside the circle: "min_lat" (drop anything south of it, e.g. the river)
# and "max_lon" (drop anything east of it).
# Which sites to check (default: all). rm = Rightmove, otm = OnTheMarket, zp = Zoopla, or = OpenRent,
# dx = Dexters (needs no URL: built from SEARCH, and joins the one-bed search too).
# SOURCES = ["rm", "otm", "zp", "or", "dx"]

# Optional: extra points for flats right by the search centre.
# NEAR_KX = {"miles": 0.5, "bonus": 1}

# Optional: a second search for one-beds that are amazing or big. They ping at min_score or
# better, go to the digest one point under (budget and commute rules still apply). Run each
# search with 1 bedroom and paste the URLs.
# ONE_BED = {
#     "max_miles": 2, "min_score": 8, "min_price": 1750, "max_price": 3000,
#     "urls": {"rm": "https://www.rightmove.co.uk/property-to-rent/find.html?...&minBedrooms=1&maxBedrooms=1&radius=0.5",
#              "otm": "https://www.onthemarket.com/to-rent/1-bed-property/some-area/?max-bedrooms=1&sort-field=update_date"},
# }

# ---- People ----
HIS_NAME = "Alex"
HER_NAME = "Sam"

# ---- Rent ----
BUDGET = 2500                 # realistic budget; the search itself can go higher
STRETCH_BUDGET = 2600         # a little over budget, up to this...
STRETCH_MIN_SCORE = 8         # ...pings instantly at this score
OVER_BUDGET_MIN_SCORE = 9     # further over budget only pings instantly if it's exceptional

# ---- Commutes (fastest route, weekday morning) ----
HIS_WORK = "EC1A1BB"         # postcode
HIS_WORK_NAME = "the office"
HIS_DEPART = "0830"
HIS_MAX_MINUTES = 50
# Hard filter only fires when TfL's BEST route is longer than this AND the
# listing pin is exact. The 5 min of slack absorbs pin/rounding error so a
# 51-minute flat is never thrown away; 50-55 lands in the digest with a note.
HIS_HARD_LIMIT_MINUTES = 55

# Second person: fastest route to a station, then the last mile by folding bike.
HER_WORK_NAME = "work"
HER_DEPART = "0730"
HER_STATION = "910GXXXXXX"    # TfL StopPoint id of the station she uses
HER_STATION_NAME = "Station"
HER_LAST_MILE_BIKE_MIN = 15   # station -> work by bike
HER_MAX_MINUTES = 80          # over this, only an exceptional flat (OVER_BUDGET_MIN_SCORE) pings instantly
FOLD_BUFFER_MIN = 3           # fold/unfold the bike at the London station
STATION_ENTRY_MAX_MIN = 4     # entrance -> platform with a folded bike (TfL says up to 11 at St Pancras)
BIKE_SLOWDOWN = 1.0           # <1 if faster than the router thinks
BIKE_TO_STATION_MAX_KM = 4    # only consider cycling to stations this close
# Thameslink stations she could cycle to (name, lat, lon).
THAMESLINK_STATIONS = [
    ("St Pancras", 51.531683, -0.123538),
    ("Farringdon", 51.520214, -0.105054),
    ("City Thameslink", 51.513936, -0.10359),
    ("Blackfriars", 51.509613, -0.104166),
    ("Kentish Town", 51.550409, -0.140545),
    ("West Hampstead Thameslink", 51.548476, -0.191837),
    ("London Bridge", 51.505881, -0.086807),
    ("Elephant & Castle", 51.494505, -0.099185),
]

# ---- Hockey (shown on each flat, never filters or scores) ----
# Fastest TfL route on a weekday evening (training); matches are similar.
HOCKEY_DEPART = "1830"
HOCKEY_VENUES = [
    ("Club ground", "51.5000,-0.1200"),   # name, "lat,lon"
]

# ---- Routing ----
INSTANT_MIN_SCORE = 7         # send straight away at or above this (within budget)...
# ...but only when outdoor space is confirmed to be one of these.
OUTDOOR_OK = {"private_balcony", "terrace", "roof_terrace", "private_garden", "communal_garden"}
# Optional: your move-in window (earliest, latest). Messages then say whether each
# flat's available date fits it.
# MOVE_IN = ("2027-01-01", "2027-01-31")
DIGEST_HOUR_LONDON = 18       # daily "near misses" message
DIGEST_MIN_SCORE = 6          # only list flats scoring this or more (count the rest)
DIGEST_MAX_ITEMS = 10
QUIET_FROM_HOUR = 23          # messages still arrive overnight, just without a buzz
QUIET_UNTIL_HOUR = 7

# ---- AI scoring (runs on your Claude subscription via `claude -p`) ----
SCREEN_MODEL = "claude-haiku-5-5"   # reads every listing
JUDGE_MODEL = "claude-opus-5-5"     # re-reads anything worth a second look
ESCALATE_MIN_SCORE = 5              # Haiku 5+ (or unsure about outdoor space) -> Opus
MAX_PHOTOS = 10                     # plus every floorplan
PHOTO_SIZE = "size656x437"          # big enough to tell a balcony from a Juliet

# What the two of you are looking for. The AI only RANKS against this;
# it never removes a listing. Edit freely.
PREFERENCES = """
We are a couple renting a 2 bed flat in London. Budget about £2,500 pcm.
Must-ish: some outdoor space (a Juliet balcony does NOT count).
Pluses: natural light, high ceilings, a big living room, a second bedroom that fits a desk.
Shorter commutes are better.
"""
