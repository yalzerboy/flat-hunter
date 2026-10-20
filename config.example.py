"""Everything you might want to tweak lives here."""

# Your Rightmove search: paste the URL of a saved search, keep sortType=6 (newest first).
# sortType=6 = newest first, which is what lets each run stop early.
SEARCH_URL = (
    "https://www.rightmove.co.uk/property-to-rent/find.html"
    "?locationIdentifier=STATION%5E0000&sortType=6&minBedrooms=2&maxPrice=3000&radius=3"
)

# ---- People ----
HIS_NAME = "Alex"
HER_NAME = "Sam"

# ---- Rent ----
BUDGET = 2500                 # realistic budget; the search itself can go higher
OVER_BUDGET_MIN_SCORE = 9     # over budget only pings instantly if it's exceptional

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
