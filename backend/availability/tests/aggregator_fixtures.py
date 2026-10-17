"""Fixture payloads for the JustWatch + Watchmode aggregator tests.

Everything here is **recorded from the live APIs** (September 2026) and is
served through injected ``http_post`` / ``http_get`` seams, so no test in the
suite ever touches the network. Shapes were trimmed, values preserved.

``JW_CATALOGUE_NL_NETFLIX`` reproduces the measured provider+country+year
partition: a Netflix-filtered page whose *offers* still span several providers
(``packages`` filters titles, not offers), one title with an inlined
``amazonprimevideo`` package, and one with no offers at all.
"""
from __future__ import annotations

import json


class FakeResponse:
    """Minimal requests-like response."""

    def __init__(self, status_code=200, payload=None, content_type="application/json",
                 text=None, headers=None):
        self.status_code = status_code
        self._payload = payload
        self.content_type = content_type
        self._text = text
        self.headers = {"content-type": content_type}
        if headers:
            self.headers.update(headers)

    def json(self):
        if self._payload is None:
            raise ValueError("no json payload")
        return self._payload

    @property
    def text(self):
        if self._text is not None:
            return self._text
        return json.dumps(self._payload) if self._payload is not None else ""


def package(technical_name, clear_name, short_name, slug=None, mtypes=("FLATRATE",)):
    return {
        "id": f"pkg-{technical_name}",
        "packageId": 1,
        "clearName": clear_name,
        "technicalName": technical_name,
        "shortName": short_name,
        "slug": slug or technical_name,
        "monetizationTypes": list(mtypes),
        "icon": "/icon.png",
    }


def offer(offer_id, technical_name, clear_name, short_name,
          monetization="FLATRATE", presentation="HD", audio=None, subs=None,
          vtech=None, url=None, package_id=None, available_to=None):
    return {
        "id": offer_id,
        "monetizationType": monetization,
        "presentationType": presentation,
        "type": "STREAMING",
        "retailPrice": None,
        "retailPriceValue": None,
        "currency": "EUR",
        "standardWebURL": url or f"https://example.test/{offer_id}",
        "elementCount": 0,
        "availableTo": available_to,
        "subtitleLanguages": subs if subs is not None else [],
        "videoTechnology": vtech if vtech is not None else [],
        "audioTechnology": [],
        "audioLanguages": audio if audio is not None else [],
        "package": package(technical_name, clear_name, short_name),
    }


def title(node_id, title, object_type="MOVIE", year=2025, tmdb_id="550",
          imdb_id="tt0137523", offers=None, season_number=None, episode_number=None,
          
          full_path="/nl/movie/x", release_date="2025-01-01", runtime=100,
          total_season_count=None, total_episode_count=None, genres=None):
    content = {
        "title": title,
        "originalReleaseYear": year,
        "originalReleaseDate": release_date,
        "runtime": runtime,
        "shortDescription": "desc",
        "seasonNumber": season_number,
        "episodeNumber": episode_number,
    }
    if object_type in ("MOVIE", "SHOW", "SEASON"):
        content["fullPath"] = full_path
        content["genres"] = [{"shortName": g} for g in (genres or [])]
        content["externalIds"] = {"imdbId": imdb_id, "tmdbId": tmdb_id}
        content["posterUrl"] = "/poster.jpg"
    node = {
        "id": node_id,
        "objectId": node_id,
        "objectType": object_type,
        "content": content,
        "offers": offers or [],
    }
    if total_season_count is not None:
        node["totalSeasonCount"] = total_season_count
    if total_episode_count is not None:
        node["totalEpisodeCount"] = total_episode_count
    return node


def edges(*nodes):
    return [{"node": n} for n in nodes]


def envelope(payload):
    return {"data": payload}


# --- JustWatch fixtures ----------------------------------------------------
NETFLIX_PKG = package("netflix", "Netflix", "nfx", "netflix")
PRIME_NL_PKG = package("amazonprimevideo", "Amazon Prime Video", "prv", "amazon-prime-video")
PRIME_US_PKG = package("amazonprime", "Amazon Prime Video", "amp", "amazon-prime-video")
ITUNES_PKG = package("itunes", "Apple TV Store", "itu", "apple-tv",
                     ("BUY", "FLATRATE", "FREE", "RENT"))

#: One Netflix-filtered NL page. The first title's offers span Netflix AND
#: Prime Video AND iTunes — the measured proof that ``packages`` filters titles
#: only, so client-side offer filtering is mandatory.
JW_CATALOGUE_NL_NETFLIX = envelope({
    "popularTitles": {
        "edges": edges(
            title(
                "tm1303572", "Superman", year=2025, tmdb_id="1061474",
                imdb_id="tt5950044",
                offers=[
                    offer("o1", "netflix", "Netflix", "nfx", "FLATRATE", "HD",
                          url="https://www.netflix.com/title/83077833"),
                    offer("o2", "amazonprimevideo", "Amazon Prime Video", "prv",
                          "FLATRATE", "_4K", audio=["en", "nl"],
                          subs=["nl", "en"], vtech=["DOLBY_VISION"]),
                    offer("o3", "itunes", "Apple TV Store", "itu", "RENT", "_4K",
                          audio=["en"], subs=["nl"]),
                ],
            ),
            title(
                "tm2873958", "The Whisper Man", year=2026, tmdb_id="860508",
                imdb_id="tt11561116",
                offers=[offer("o4", "netflix", "Netflix", "nfx", "FLATRATE", "HD")],
            ),
        )
    }
})

#: A page whose only title carries an unmapped provider and no offers.
JW_CATALOGUE_UNMAPPED_ONLY = envelope({
    "popularTitles": {
        "edges": edges(
            title("tm999", "Obscure", offers=[
                offer("o9", "wug", "Weird Unknown Service", "wug")]),
        )
    }
})

JW_CATALOGUE_EMPTY = envelope({"popularTitles": {"edges": []}})

#: Rakuten TV's JustWatch id is still the pre-rebrand ``wuaki``. This is the
#: measured NL shape for a title carried free-with-ads: an ``ADS`` offer on
#: Rakuten TV plus a ``BUY`` offer on the Apple store (excluded by design).
WUAKI_PKG = package("wuaki", "Rakuten TV", "rkt", "rakuten-tv", ("ADS", "BUY"))

JW_SEARCH_RAKUTEN_NL = envelope({
    "popularTitles": {
        "edges": edges(
            title("tm116488", "Natural Born Killers", year=1994, tmdb_id="241",
                  imdb_id="tt0110632", full_path="/nl/movie/natural-born-killers",
                  offers=[
                      offer("r1", "wuaki", "Rakuten TV", "rkt", "ADS", "HD"),
                      offer("r2", "itunes", "Apple TV Store", "itu", "BUY", "HD"),
                  ]),
        )
    }
})

JW_SEARCH_MOVIE = envelope({
    "popularTitles": {
        "edges": edges(
            title("tm1303572", "Superman", year=2025, tmdb_id="1061474",
                  imdb_id="tt5950044",
                  offers=[offer("o1", "netflix", "Netflix", "nfx", "FLATRATE", "HD")]),
            title("tm111", "Superman II", year=2026, tmdb_id="999999",
                  imdb_id="tt5950045",
                  offers=[offer("o2", "netflix", "Netflix", "nfx")]),
        )
    }
})

JW_SEARCH_SHOW = envelope({
    "popularTitles": {
        "edges": edges(
            title("ts258721", "The Gentlemen", object_type="SHOW", year=2024,
                  tmdb_id="236235", imdb_id="tt13210838",
                  full_path="/nl/tv-series/the-gentlemen",
                  total_season_count=2,
                  offers=[offer("o5", "netflix", "Netflix", "nfx", "FLATRATE", "HD")]),
        )
    }
})

JW_SEASONS = {
    # Nested under "node": that is the shape the endpoint actually returns for
    # a `... on Show { seasons(...) }` selection.
    "node": {"seasons": [
        title("tss351167", "Season 1", object_type="SEASON", year=2024,
              tmdb_id="236235", season_number=1, total_episode_count=8,
              offers=[offer("o6", "netflix", "Netflix", "nfx", "FLATRATE", "HD")]),
        title("tss459374", "Season 2", object_type="SEASON", year=2025,
              tmdb_id="236235", season_number=2, total_episode_count=8,
              offers=[offer("o7", "netflix", "Netflix", "nfx", "FLATRATE", "HD")]),
    ]},
}

JW_EPISODES = {
    "node": {"episodes": [
        title("tse6497693", "Refined Aggression", object_type="SHOW_EPISODE",
              season_number=1, episode_number=1, imdb_id=None,
              offers=[offer("o8", "netflix", "Netflix", "nfx", "FLATRATE", "HD")]),
    ]},
}

JW_OFFERS_MULTI_COUNTRY = {
    "node": {
        "NL": [offer("nl1", "netflix", "Netflix", "nfx", "FLATRATE", "HD")],
        "US": [offer("us1", "amazonprime", "Amazon Prime Video", "amp",
                     "FLATRATE", "HD")],
        "DE": [],
    }
}

JW_PROVIDERS_NL = envelope({
    "packages": [
        NETFLIX_PKG, PRIME_NL_PKG, ITUNES_PKG,
        package("raiplay", "Rai Play", "rai", "rai-play", ("ADS", "FLATRATE", "FREE")),
        package("nosuchprovider", "Totally Unknown", "tux"),
    ]
})

JW_ERRORS_PAGE_TOO_LARGE = {
    "errors": [{"message": "page too large"}], "data": None}
JW_ERRORS_COMPLEXITY = {
    "errors": [{"message": "operation has complexity 350500, which exceeds the "
                           "limit of 350000"}], "data": None}
JW_ERRORS_INVALID = {"errors": [{"message": "transport not supported"}], "data": None}

# --- Watchmode fixtures ----------------------------------------------------
#: Recorded shape: /search/ returns a DICT, and /title/<id>/sources/ rows have
#: `type` (no offer_type / streaming_type).
WM_SEARCH_MOVIE = {
    "success": True,
    "title_results": [
        {"resultType": "title", "id": 1132806, "name": "Fight Club", "type": "movie",
         "year": 1999, "imdb_id": "tt0137523", "tmdb_id": 550, "tmdb_type": "movie"},
    ],
    "people_results": [],
}

WM_SEARCH_EMPTY = {"success": True, "title_results": [], "people_results": []}

WM_SOURCES_NL = [
    {"source_id": 203, "name": "Netflix", "type": "sub", "region": "NL",
     "web_url": "https://www.netflix.com/title/26004747", "format": "HD",
     "price": None, "seasons": 0, "episodes": 0},
    {"source_id": 55, "name": "Apple TV Store", "type": "rent", "region": "NL",
     "web_url": "https://tv.apple.com/nl/movie/x", "format": "4K",
     "price": 4.99, "seasons": 0, "episodes": 0},
    {"source_id": 77, "name": "Mystery Stream", "type": "sub", "region": "NL",
     "web_url": None, "format": "HD", "price": None, "seasons": 0, "episodes": 0},
]

WM_SOURCES_MULTI = [
    {"source_id": 203, "name": "Netflix", "type": "sub", "region": "NL",
     "web_url": "u1", "format": "HD", "price": None},
    {"source_id": 203, "name": "Netflix", "type": "sub", "region": "CA",
     "web_url": "u2", "format": "HD", "price": None},
    {"source_id": 203, "name": "Netflix", "type": "sub", "region": "JP",
     "web_url": "u3", "format": "HD", "price": None},
    {"source_id": 12, "name": "Amazon Prime Video", "type": "buy", "region": "NL",
     "web_url": "u4", "format": "HD", "price": 5.0},
    {"source_id": 13, "name": "Tubi", "type": "free", "region": "CA",
     "web_url": "u5", "format": "HD", "price": None},
]

WM_QUOTA_HEADERS = {
    "x-account-quota": "2500",
    "x-account-quota-used": "14",
    "x-ratelimit-limit": "1000",
    "x-ratelimit-remaining": "986",
}

WM_ERROR_REGION = {"success": False, "statusCode": 400,
                   "statusMessage": "US is not enabled for your current plan."}
WM_ERROR_QUOTA = {"success": False, "statusCode": 429,
                  "statusMessage": "Quota exceeded for the current month."}