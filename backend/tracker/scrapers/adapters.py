"""Site-specific scraper adapters for KissKH, HydraHD, and AnimeWorld."""
from __future__ import annotations

import logging
import re
from datetime import datetime
from typing import Any, Optional
from urllib.parse import urlencode, urljoin

from bs4 import BeautifulSoup, Tag

from tracker.scrapers.base import BaseScraper, ScrapedItem

logger = logging.getLogger(__name__)

#: ``Anno: 2007`` — the year label AnimeWorld prints inside a result card. The
#: negative lookahead keeps a malformed run of digits from being truncated to
#: its first four characters.
ANIMEWORLD_YEAR_RE = re.compile(r"Anno\s*:\s*(\d{4})(?!\d)", re.IGNORECASE)
#: A standalone four-digit year inside a HydraHD result anchor.
HYDRA_YEAR_RE = re.compile(r"(?<!\d)(\d{4})(?!\d)")


def _valid_year(value: Any) -> Optional[int]:
    """Return *value* as a plausible release year, or ``None``.

    An implausible value is unknown rather than ``0``/``None``-by-accident:
    callers must never fill the gap with a neighbouring result's year.
    """
    try:
        year = int(value)
    except (TypeError, ValueError):
        return None
    return year if 1900 <= year <= 2100 else None


def _query_url(base: str, **params: Any) -> str:
    """Append query parameters with correct percent-encoding.

    ``urlencode`` (or ``requests``' ``params=``) is the only thing that keeps a
    search term's ``&``/``+``/spaces from changing the query structure.
    """
    return f"{base}?{urlencode(params)}"


def _hydra_title_div(scope: Tag) -> Optional[Tag]:
    """First ``div.title.detz`` inside *scope*, if any."""
    for div in scope.find_all("div"):
        classes = div.get("class") or []
        if "title" in classes and "detz" in classes:
            return div
    return None


def _content_type_from_url(url: Any, default: str = "movie") -> str:
    """Classify an item from its URL path.

    HydraHD keeps films and shows in separate path namespaces (`/movie/...`
    vs `/watchseries/...`), so the URL carries a usable signal even for search
    results, where no browse category is available. Anything unrecognised keeps
    ``default`` — an unknown signal must never become a series.
    """
    path = str(url or "").split("?", 1)[0]
    if "watchseries/" in path:
        return "series"
    if "/movie/" in path or path.startswith("movie/"):
        return "movie"
    return default


class KissKHScraper(BaseScraper):
    provider_slug = "kisskh"

    # Both hosts serve the same API; keep a fallback so a domain move does not
    # silently zero out the scraper.
    API_BASES = ("https://kisskh.do", "https://kisskh.co")
    # The API defaults to 10 rows per page. `pageSize` is accepted and lets a
    # single request return hundreds of titles instead of ten.
    PAGE_SIZE = 200
    PAGES_PER_TYPE = 3
    DISCOVERY_PAGE_SIZE = 20

    def _fetch_api(self, path: str):
        for base in self.API_BASES:
            data = self.fetch_url(f"{base}{path}")
            if data:
                return data
        return None

    def fetch_detail(self, native_id: str | int):
        """Fetch optional title metadata through KissKH's public detail API."""
        from urllib.parse import quote

        return self._fetch_api(f"/api/DramaList/Drama/{quote(str(native_id), safe='')}?isq=false")

    @staticmethod
    def extract_detail_metadata(detail: Any, *, native_id: str | None = None) -> dict[str, Any]:
        """Return only explicit detail fields; episode counts never imply type."""
        if not isinstance(detail, dict):
            return {}
        provider_id = detail.get("id")
        if native_id is not None and provider_id is not None and str(provider_id) != str(native_id):
            return {}
        release = detail.get("releaseDate") or detail.get("release_date")
        year = detail.get("year") or detail.get("releaseYear")
        if not year and release:
            try:
                year = datetime.fromisoformat(str(release).replace("Z", "+00:00")).year
            except ValueError:
                year = None
        try:
            year = int(year) if year else None
        except (TypeError, ValueError):
            year = None
        if year is not None and not 1900 <= year <= 2100:
            year = None
        episodes = detail.get("episodes")
        episode_ids = []
        if isinstance(episodes, list):
            for episode in episodes:
                if isinstance(episode, dict):
                    episode_id = episode.get("id") or episode.get("episodeId") or episode.get("episodeID")
                    if episode_id is not None:
                        episode_ids.append(str(episode_id))
        try:
            episode_count = int(detail.get("episodesCount"))
        except (TypeError, ValueError):
            episode_count = len(episodes) if isinstance(episodes, list) else None
        return {
            "native_id": str(provider_id if provider_id is not None else native_id or ""),
            "title": str(detail.get("title") or detail.get("name") or ""),
            "original_title": str(detail.get("originalTitle") or detail.get("original_title") or ""),
            "release_date": str(release or ""),
            "release_year": year,
            "source_media_type": str(detail.get("type") or ""),
            "episode_count": episode_count,
            "episode_ids": episode_ids,
            "detail_url": f"https://kisskh.do/Drama/{provider_id if provider_id is not None else native_id}",
            "raw": detail,
        }

    # KissKH's API `type` parameter is the site's own structured content flag and
    # 2 = movie partition, 1 = TV-series partition, 3 = anime partition. `type=0`
    # is avoided because it carries no partition signal. The anime partition is
    # mixed and single-episode anime remains unclassified unless detail metadata
    # provides an explicit `Movie` or `TVSeries` type.
    BROWSE_TYPES: tuple[tuple[str, str], ...] = (
        ("2", "movie"),
        ("1", "series"),
        # The provider calls this partition "Anime". It contains both films
        # and multi-episode works, so it is not itself a movie/series signal.
        ("3", "anime"),
    )

    def _execute_run(self, query: Optional[str] = None) -> list[ScrapedItem]:
        items: list[ScrapedItem] = []

        if not query:
            logger.info("KissKH scraper: no query provided, scraping recently added...")
            for type_code, content_type in self.BROWSE_TYPES:
                for page in range(1, self.PAGES_PER_TYPE + 1):
                    path = (
                        f"/api/DramaList/List?page={page}&type={type_code}"
                        f"&sub=0&country=0&status=0&order=1&pageSize={self.PAGE_SIZE}"
                    )
                    logger.info("KissKH scraper: fetching %s", path)
                    resp_data = self._fetch_api(path)
                    if not resp_data or not isinstance(resp_data, dict):
                        break
                    data = resp_data.get("data")
                    if not data:
                        break
                    page_items = self.parse_items(data, content_type=content_type)
                    if not page_items:
                        break
                    items.extend(page_items)
                    if len(data) < self.PAGE_SIZE:
                        break  # Last page for this type
            return self._dedupe(items)

        for type_code, content_type in self.BROWSE_TYPES:
            url = _query_url(
                "https://kisskh.do/api/DramaList/Search",
                q=query, type=type_code,
            )
            resp_data = self.fetch_url(url)
            if not resp_data:
                logger.warning("KissKH scraper: no response data for type=%s", type_code)
                continue
            if not isinstance(resp_data, list):
                logger.warning(
                    "KissKH scraper: expected list for type=%s, got %s",
                    type_code,
                    type(resp_data),
                )
                continue
            logger.info("KissKH scraper: got %d entries", len(resp_data))
            items.extend(self.parse_items(resp_data, content_type=content_type))
        return self._dedupe(items)

    @staticmethod
    def _dedupe(items: list[ScrapedItem]) -> list[ScrapedItem]:
        """Drop rows seen under another type request (the same title can be
        returned by both the series and the film query)."""
        seen: set[Any] = set()
        deduped: list[ScrapedItem] = []
        for item in items:
            payload = item.raw_payload
            entry_id = payload.get("id") if isinstance(payload, dict) else None
            key = (
                (str(entry_id), item.content_type)
                if entry_id is not None
                else (item.title, item.year, item.content_type)
            )
            if key in seen:
                continue
            seen.add(key)
            deduped.append(item)
        return deduped

    def parse_items(self, raw_data: Any, content_type: str = "movie") -> list[ScrapedItem]:
        items: list[ScrapedItem] = []
        if not isinstance(raw_data, list):
            return items
        for entry in raw_data:
            if not isinstance(entry, dict):
                continue
            title = entry.get("title") or entry.get("name")
            native_id = entry.get("id")
            if not title or native_id is None or isinstance(native_id, (dict, list, bool)):
                continue
            native_id = str(native_id).strip()
            if not native_id:
                continue
            year = entry.get("year") or entry.get("releaseYear")
            if not year and entry.get("releaseDate"):
                try:
                    year = datetime.fromisoformat(
                        str(entry["releaseDate"]).replace("Z", "+00:00")
                    ).year
                except ValueError:
                    year = None
            try:
                year_int = int(year) if year else None
            except (ValueError, TypeError):
                year_int = None
            if year_int is not None and not 1900 <= year_int <= 2100:
                year_int = None
            provider_type = str(entry.get("type") or "").strip().casefold().replace(" ", "")
            detail_types = {
                "movie": "movie", "tvseries": "series", "tv_series": "series",
                "tv-series": "series", "series": "series",
            }
            item_type = detail_types.get(provider_type)
            if item_type is None:
                # Partition membership and episode counts are not title-level
                # classification evidence. The discovery path stores these
                # rows as pending until detail metadata supplies Movie/TVSeries.
                continue

            # Missing fields mean the provider did not report metadata.  Do
            # not turn that absence into an English/1080p claim: ranking treats
            # those values as evidence and would otherwise prefer this offer.
            audio = entry.get("audio_languages") or entry.get("audio") or []
            subs = entry.get("subtitle_languages") or entry.get("subtitles") or []
            quality = entry.get("quality")
            url_path = entry.get("url") or f"https://kisskh.do/Drama/{native_id}"

            items.append(
                ScrapedItem(
                    title=str(title),
                    provider_slug=self.provider_slug,
                    year=year_int,
                    content_type=item_type,
                    source_url=str(url_path),
                    audio_languages=audio if isinstance(audio, list) else [str(audio)],
                    subtitle_languages=subs if isinstance(subs, list) else [str(subs)],
                    quality=str(quality) if quality else None,
                    availability=True,
                    raw_payload=entry,
                )
            )
        return items

    def parse_catalogue_rows(self, rows: Any) -> tuple[list[ScrapedItem], list[dict[str, Any]]]:
        """Split listing records into explicitly typed items and pending rows."""
        if not isinstance(rows, list):
            return [], []
        classified, pending = [], []
        for row in rows:
            if not isinstance(row, dict):
                continue
            items = self.parse_items([row])
            if items:
                classified.extend(items)
            else:
                title = row.get("title") or row.get("name")
                native_id = row.get("id")
                if title and native_id is not None and not isinstance(native_id, (dict, list, bool)) and str(native_id).strip():
                    pending.append(row)
        return self._dedupe(classified), pending


class GenericURLAdapter(BaseScraper):
    """Fallback adapter for dynamically registered provider domains."""

    def __init__(self, provider_slug: str, domain: str, timeout: Optional[int] = None):
        super().__init__(timeout=timeout)
        self.provider_slug = provider_slug
        self.domain = domain

    def _execute_run(self, query: Optional[str] = None) -> list[ScrapedItem]:
        target_url = (
            _query_url(f"https://{self.domain}/api/search", q=query)
            if query else f"https://{self.domain}"
        )
        resp_data = self.fetch_url(target_url)

        if isinstance(resp_data, list):
            return self.parse_items(resp_data)
        elif isinstance(resp_data, dict):
            items = resp_data.get("results") or resp_data.get("items") or resp_data.get("data") or []
            return self.parse_items(items)
        return []

    def parse_items(self, raw_data: Any) -> list[ScrapedItem]:
        items: list[ScrapedItem] = []
        for entry in raw_data or []:
            if not isinstance(entry, dict):
                continue
            title = entry.get("title") or entry.get("name")
            if not title:
                continue
            year = entry.get("year")
            try:
                year_int = int(year) if year else None
            except (ValueError, TypeError):
                year_int = None

            audio = entry.get("audio_languages") or entry.get("audio") or []
            subs = entry.get("subtitle_languages") or entry.get("subtitles") or []
            quality = entry.get("quality") or entry.get("res")
            url_path = entry.get("url") or entry.get("link") or f"https://{self.domain}/view/{entry.get('id')}"

            items.append(
                ScrapedItem(
                    title=str(title),
                    provider_slug=self.provider_slug,
                    year=year_int,
                    source_url=str(url_path),
                    audio_languages=audio if isinstance(audio, list) else [str(audio)],
                    subtitle_languages=subs if isinstance(subs, list) else [str(subs)],
                    quality=str(quality) if quality else None,
                    availability=True,
                    raw_payload=entry,
                )
            )
        return items


class HydraHDScraper(BaseScraper):
    provider_slug = "hydrahd"

    # hydrahd.ws now redirects to hydrahd.gd; list the canonical host first and
    # keep the old one as a fallback. hydrahd.ru serves a stub page, so it is
    # deliberately excluded.
    DOMAINS = ("hydrahd.gd", "hydrahd.ws")
    PAGES_PER_CATEGORY = 10

    HEADERS = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "en-US,en;q=0.9",
        "Referer": "https://hydrahd.gd/",
    }

    def _execute_run(self, query: Optional[str] = None) -> list[ScrapedItem]:
        if not query:
            logger.info("HydraHD scraper: no query provided, scraping recently added...")
            items = []
            for category in ["movies", "tv-shows"]:
                # The browse category only acts as the fallback signal; the item URL
                # itself is authoritative for classification.
                default_content_type = "series" if category == "tv-shows" else "movie"
                for page in range(1, self.PAGES_PER_CATEGORY + 1):
                    for domain in self.DOMAINS:
                        url = f"https://{domain}/{category}/date/{page}"
                        logger.info("HydraHD scraper: fetching %s", url)
                        resp_data = self.fetch_url(url, headers=self.HEADERS)
                        if resp_data and isinstance(resp_data, str):
                            page_items = self.parse_html(
                                resp_data,
                                default_content_type=default_content_type,
                                base_url=f"https://{domain}",
                            )
                            if page_items:
                                items.extend(page_items)
                                break  # This domain worked for this page
            return self._dedupe(items)

        # Try the current domain first, fall back to the old one
        urls_to_try = [
            _query_url(f"https://{domain}/index.php", menu="search", query=query)
            for domain in self.DOMAINS
        ]

        for url in urls_to_try:
            logger.info("HydraHD scraper: trying %s", url)
            resp_data = self.fetch_url(url, headers=self.HEADERS)
            if resp_data:
                if isinstance(resp_data, dict):
                    results = resp_data.get("results") or []
                    logger.info("HydraHD scraper: got %d results from JSON %s", len(results), url)
                    return self.parse_items(results)
                elif isinstance(resp_data, str):
                    items = getattr(self, "parse_html", lambda x: [])(resp_data)
                    logger.info("HydraHD scraper: got %d results from HTML %s", len(items), url)
                    if items:
                        return items

        logger.warning("HydraHD scraper: all URLs failed")
        return []

    def parse_items(self, raw_data: Any) -> list[ScrapedItem]:
        items: list[ScrapedItem] = []
        for entry in raw_data or []:
            if not isinstance(entry, dict):
                continue
            title = entry.get("title")
            if not title:
                continue
            year = entry.get("year")
            try:
                year_int = int(year) if year else None
            except (ValueError, TypeError):
                year_int = None

            audio = entry.get("audio") or []
            subs = entry.get("subtitles") or []
            quality = entry.get("res")
            source_url = entry.get("link") or f"https://hydrahd.ru/watch/{entry.get('id')}"

            items.append(
                ScrapedItem(
                    title=str(title),
                    provider_slug=self.provider_slug,
                    year=year_int,
                    content_type=_content_type_from_url(source_url),
                    source_url=str(source_url),
                    audio_languages=audio if isinstance(audio, list) else [str(audio)],
                    subtitle_languages=subs if isinstance(subs, list) else [str(subs)],
                    quality=str(quality) if quality else None,
                    availability=True,
                    raw_payload=entry,
                )
            )
        return items

    @staticmethod
    def _dedupe(items: list[ScrapedItem]) -> list[ScrapedItem]:
        """Drop titles repeated across browse pages."""
        seen: set[str] = set()
        deduped: list[ScrapedItem] = []
        for item in items:
            if item.source_url in seen:
                continue
            seen.add(item.source_url)
            deduped.append(item)
        return deduped

    def parse_html(
        self,
        html_content: str,
        default_content_type: str = "movie",
        base_url: Optional[str] = None,
    ) -> list[ScrapedItem]:
        r"""Pair each result's link with its own title, never a neighbour's.

        The old single regex started at an ``a.hthis`` anchor and then used
        ``[\s\S]*?`` to reach the next ``div.title.detz``. A card without a
        title therefore consumed the *next* card's title (and vice versa), so a
        title could be published under another result's URL. Anchors are now
        parsed structurally and the title search stops at the next anchor.
        """
        items: list[ScrapedItem] = []
        soup = BeautifulSoup(html_content or "", "html.parser")
        for anchor in soup.find_all("a", class_="hthis"):
            if anchor.find_parent("a", class_="hthis"):
                # A nested anchor belongs to the outer result, not a new one.
                continue
            href = str(anchor.get("href") or "").strip()
            if not href:
                logger.info("HydraHD scraper: result skipped reason=missing_href")
                continue
            title = self._result_title(anchor)
            if not title:
                logger.info(
                    "HydraHD scraper: result skipped href=%s reason=missing_title",
                    href,
                )
                continue
            year_int = self._result_year(anchor)
            source_url = urljoin(base_url or "https://hydrahd.ws/", href)
            items.append(
                ScrapedItem(
                    title=title,
                    provider_slug=self.provider_slug,
                    year=year_int,
                    content_type=_content_type_from_url(
                        source_url, default=default_content_type
                    ),
                    source_url=source_url,
                    audio_languages=[],
                    subtitle_languages=[],
                    quality=None,
                    availability=True,
                    raw_payload={"href": href, "title": title, "year": year_int},
                )
            )
        return items

    @staticmethod
    def _result_title(anchor: Tag) -> str:
        """The title belonging to *anchor*'s result card.

        First the smallest ancestor that isolates exactly this result; if the
        card has no wrapper, walk forward but never past the next ``hthis``
        anchor. Both paths keep the title inside one result.
        """
        for ancestor in anchor.parents:
            if not isinstance(ancestor, Tag):
                break
            classes = ancestor.get("class") or []
            if ancestor.name == "div" and "title" in classes and "detz" in classes:
                return ancestor.get_text(" ", strip=True)
            if len(ancestor.find_all("a", class_="hthis")) > 1:
                break
            title = _hydra_title_div(ancestor)
            if title:
                return title.get_text(" ", strip=True)
        for element in anchor.next_elements:
            if not isinstance(element, Tag):
                continue
            classes = element.get("class") or []
            if element.name == "a" and "hthis" in classes:
                break
            if element.name == "div" and "title" in classes and "detz" in classes:
                return element.get_text(" ", strip=True)
        return ""

    @staticmethod
    def _result_year(anchor: Tag) -> Optional[int]:
        """Year printed inside *anchor*, or ``None`` (never a neighbour's)."""
        spans = anchor.find_all("span")
        for span in spans or []:
            match = HYDRA_YEAR_RE.search(span.get_text(" ", strip=True))
            if match:
                return _valid_year(match.group(1))
        match = HYDRA_YEAR_RE.search(anchor.get_text(" ", strip=True))
        return _valid_year(match.group(1)) if match else None


class AnimeWorldScraper(BaseScraper):
    provider_slug = "animeworld"

    def parse_rss(self, xml_content: str) -> list[ScrapedItem]:
        """Parse AnimeWorld's first-party, intentionally published RSS feed."""
        import xml.etree.ElementTree as ET

        try:
            root = ET.fromstring(xml_content or "")
        except ET.ParseError:
            return []
        self.feed_last_build_date = next((n.text.strip() for n in root.iter() if n.tag.rsplit("}", 1)[-1] == "lastBuildDate" and n.text), None)
        items = []
        for node in root.findall(".//item"):
            values = {child.tag.rsplit("}", 1)[-1]: child for child in list(node)}
            def value(name):
                child = values.get(name)
                return (child.text or "").strip() if child is not None else ""
            title = value("name") or value("title")
            url = value("link")
            if not title or not url:
                continue
            guid = values.get("guid")
            media_type = value("type").lower()
            items.append(ScrapedItem(
                title=title, provider_slug=self.provider_slug,
                content_type="series" if media_type == "anime" else "movie",
                source_url=urljoin("https://www.animeworld.ac/", url),
                raw_payload={
                    "guid": (guid.text or "").strip() if guid is not None else "",
                    "pubDate": value("pubDate"), "anime_type": media_type,
                    "original_title": value("name"), "feed_title": value("title"),
                    "source_kind": "official_rss",
                    "provenance_state": "VERIFIED", "access_policy_state": "PERMITTED",
                },
            ))
        return items

    def _execute_run(self, query: Optional[str] = None) -> list[ScrapedItem]:
        if not query:
            logger.info("AnimeWorld scraper: no query provided, scraping movies and animes...")
            items = []
            for category in ["movies", "animes"]:
                for page in range(1, 3):
                    url = f"https://www.animeworld.ac/{category}?{urlencode({'page': page})}"
                    logger.info("AnimeWorld scraper: fetching %s", url)
                    # Browse requests go through the shared helper so they get
                    # the same timeout, bounded retry and error handling as the
                    # search path. A direct ``session.get`` bypassed all three.
                    resp_text = self.fetch_url(
                        url, headers={"User-Agent": "Mozilla/5.0"}
                    )
                    if not resp_text or not isinstance(resp_text, str):
                        logger.warning(
                            "AnimeWorld scraper: no HTML for %s", url
                        )
                        continue
                    page_items = self.parse_html(resp_text, category=category)
                    if not page_items:
                        break
                    items.extend(page_items)
            return items

        url = _query_url("https://animeworld.ac/api/search", keyword=query)
        resp_data = self.fetch_url(url)
        if not resp_data:
            logger.warning("AnimeWorld scraper: no response data")
            return []

        # AnimeWorld API returns {"html": "<div>...</div>"} not a JSON list
        if isinstance(resp_data, dict):
            html_content = resp_data.get("html", "")
            if html_content:
                logger.info("AnimeWorld scraper: parsing HTML response (%d bytes)", len(html_content))
                return self.parse_html(html_content)
            else:
                logger.warning("AnimeWorld scraper: dict response has no 'html' key")
                return []

        logger.warning("AnimeWorld scraper: unexpected response type: %s", type(resp_data))
        return []

    def parse_html(self, html_content: str, category: Optional[str] = None) -> list[ScrapedItem]:
        """Parse HTML response containing anime items.

        ``category`` is the browse section the HTML came from. AnimeWorld has two
        static sections (`/animes` and `/movies`) and every item carries the same
        `/play/...` href, so the section is the only per-item type signal; search
        results arrive without one and keep the default movie type. An unknown
        category never becomes a series.

        Titles and years used to be collected as two independent lists and
        paired by index. One card without an ``Anno:`` label shifted every
        later year onto the previous title. Each result is now read from its
        own card, and a card without a year stays unknown.
        """
        items: list[ScrapedItem] = []
        content_type = "series" if category == "animes" else "movie"
        soup = BeautifulSoup(html_content or "", "html.parser")

        for anchor in soup.find_all("a", class_="name"):
            href = str(anchor.get("href") or "").strip()
            title = anchor.get_text(" ", strip=True)
            if not href or not title:
                logger.info(
                    "AnimeWorld scraper: result skipped href=%s reason=missing_title_or_link",
                    href,
                )
                continue
            year_int = self._result_year(anchor)
            source_url = urljoin("https://www.animeworld.ac/", href)

            items.append(
                ScrapedItem(
                    title=title,
                    provider_slug=self.provider_slug,
                    year=year_int,
                    content_type=content_type,
                    source_url=source_url,
                    # Search markup carries no playback metadata. Site locale
                    # and genre are not evidence of an item's tracks or max
                    # rendition, so keep every field unknown.
                    audio_languages=[],
                    subtitle_languages=[],
                    quality=None,
                    availability=True,
                    raw_payload={"href": href, "title": title, "year": year_int},
                )
            )

        logger.info("AnimeWorld scraper: parsed %d items", len(items))
        return items

    @staticmethod
    def _result_year(anchor: Tag) -> Optional[int]:
        """The ``Anno:`` year inside *anchor*'s own card, if one exists.

        Walking up to the smallest ancestor that contains only this result
        means a card without a year cannot borrow the next card's. Crossing a
        boundary (more than one ``a.name`` in the ancestor) stops the search.
        """
        for ancestor in anchor.parents:
            if not isinstance(ancestor, Tag):
                break
            if len(ancestor.find_all("a", class_="name")) > 1:
                break
            match = ANIMEWORLD_YEAR_RE.search(ancestor.get_text(" ", strip=True))
            if match:
                return _valid_year(match.group(1))
        return None

    def parse_items(self, raw_data: Any) -> list[ScrapedItem]:
        """Legacy method kept for compatibility. New implementation uses parse_html."""
        logger.warning("AnimeWorld parse_items called with legacy format")
        items: list[ScrapedItem] = []
        for entry in raw_data or []:
            if not isinstance(entry, dict):
                continue
            title = entry.get("name") or entry.get("title")
            if not title:
                continue
            year = entry.get("year")
            try:
                year_int = int(year) if year else None
            except (ValueError, TypeError):
                year_int = None

            audio = entry.get("dub") or []
            subs = entry.get("sub") or []
            source_url = entry.get("link") or f"https://www.animeworld.ac/play/{entry.get('id')}"

            items.append(
                ScrapedItem(
                    title=str(title),
                    provider_slug=self.provider_slug,
                    year=year_int,
                    source_url=str(source_url),
                    audio_languages=audio if isinstance(audio, list) else [str(audio)],
                    subtitle_languages=subs if isinstance(subs, list) else [str(subs)],
                    quality=str(entry.get("quality")) if entry.get("quality") else None,
                    availability=True,
                    raw_payload={**entry, "unverified_discovery": True},
                )
            )
        return items
