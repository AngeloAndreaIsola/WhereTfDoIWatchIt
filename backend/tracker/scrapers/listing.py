"""Shared helpers for parsing server-rendered streaming catalogue pages.

These providers render image-only tiles: the anchor has no visible text, so
``link.get_text()`` returns an empty string and the old scrapers skipped nearly
every title. Real titles live in ``data-title`` / ``title`` / ``img[alt]`` and
year/content-type in ``data-year`` / ``data-kind`` attributes. Extract from
those instead, falling back to the numeric URL slug.
"""
from __future__ import annotations

import html
import re
from typing import Optional
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup

from tracker.scrapers.base import ScrapedItem

TAG_LANGUAGES = {
    "ITA": "it",
    "ENG": "en",
    "JAP": "ja",
    "SUB-ITA": "it",
    "SUB-ENG": "en",
    "SUB-JAP": "ja",
}

_BRACKET_RE = re.compile(r"\[([^\]]+)\]")
_URL_SUFFIX_RE = re.compile(r"(?:-streaming(?:-cb01)?|-cb01|\.html|/)$")
UI_CLASS_RE = re.compile(r"\b(info-button|favorite|play2?|btn|slide-button|info-btn)\b", re.I)
UI_LABELS = {
    "la mia lista", "riproduci", "altre info", "guarda", "play",
    "watching", "the list", "my list", "more info", "details",
}


def clean_tag_title(value: str) -> str:
    """Strip [TAG] brackets and a trailing (year) from a scraped title."""
    value = _BRACKET_RE.sub("", value or "")
    value = re.sub(r"\s*\((?:19|20)\d{2}\)\s*$", "", value)
    return re.sub(r"\s+", " ", value).strip()


def languages_from_tags(text: str) -> tuple[list[str], list[str]]:
    """Return (audio, subtitle) language codes found in bracket tags."""
    audio: list[str] = []
    subs: list[str] = []
    for match in _BRACKET_RE.finditer(text or ""):
        tag = match.group(1).strip().upper()
        code = TAG_LANGUAGES.get(tag)
        if not code:
            continue
        if tag.startswith("SUB-"):
            subs.append(code)
        else:
            audio.append(code)
    return list(dict.fromkeys(audio)), list(dict.fromkeys(subs))


def title_from_anchor(anchor) -> str:
    """Read a title from the attributes/element the live sites actually use."""
    for attr in ("data-title", "title"):
        value = anchor.get(attr)
        if value and value.strip():
            return html.unescape(value.strip())
    image = anchor.find("img")
    if image:
        for attr in ("alt", "title"):
            value = image.get(attr)
            if value and value.strip():
                return html.unescape(value.strip())
    text = anchor.get_text(strip=True)
    if text:
        return html.unescape(text)
    value = anchor.get("aria-label")
    if value and value.strip():
        return html.unescape(value.strip())
    return ""


def title_from_href(href: str) -> str:
    """Derive a human title from a numeric slug URL as a last resort."""
    match = re.search(r"/\d+-([^/?#]+)", href or "")
    if not match:
        return ""
    slug = match.group(1)
    for _ in range(3):
        stripped = _URL_SUFFIX_RE.sub("", slug)
        if stripped == slug:
            break
        slug = stripped
    return html.unescape(slug.replace("-", " ")).strip()


def year_from(value: Optional[str]) -> Optional[int]:
    match = re.search(r"(?:19|20)\d{2}", value or "")
    if not match:
        return None
    try:
        return int(match.group())
    except ValueError:
        return None


def dedupe_items(items: list[ScrapedItem]) -> list[ScrapedItem]:
    """Drop repeated items across listing pages, keyed by canonical source URL."""
    seen: set[str] = set()
    deduped: list[ScrapedItem] = []
    for item in items:
        if item.source_url in seen:
            continue
        seen.add(item.source_url)
        deduped.append(item)
    return deduped


def items_from_listing(
    html_doc: str,
    *,
    href_regex: str,
    provider_slug: str,
    base_url: str,
    default_content_type: str = "movie",
    query_filter: Optional[str] = None,
    source_label: str = "listing",
) -> list[ScrapedItem]:
    """Build ScrapedItems from a catalogue listing page.

    ``href_regex`` selects detail links. Titles, years and content type are
    read from data attributes (or the slug) rather than anchor text, which is
    empty on these sites. No per-title detail page is fetched: the listings
    already carry all metadata we can rely on, and fetching hundreds of very
    large Next.js detail pages makes a scraper run unusably slow.
    """
    soup = BeautifulSoup(html_doc, "html.parser")
    items: list[ScrapedItem] = []
    seen: set[str] = set()

    for anchor in soup.find_all("a", href=True):
        href = anchor["href"]
        if not re.search(href_regex, href):
            continue

        classes = anchor.get("class") or []
        if UI_CLASS_RE.search(" ".join(classes)):
            continue

        raw_title = title_from_anchor(anchor) or title_from_href(href)
        if not raw_title or len(raw_title) < 2:
            continue
        if raw_title.strip().lower() in UI_LABELS:
            continue
        if query_filter and query_filter not in raw_title.lower():
            continue

        title = clean_tag_title(raw_title)
        if not title:
            continue

        year = year_from(anchor.get("data-year") or "")
        year = year or year_from(raw_title) or year_from(href)

        kind = (anchor.get("data-kind") or "").lower()
        series_hint = any(
            token in f"{href} {kind}".lower()
            for token in ("series", "serie-tv", "tv-show")
        )
        content_type = "series" if series_hint else default_content_type

        audio_langs, sub_langs = languages_from_tags(raw_title)
        data_sub = (anchor.get("data-sub") or "").strip()
        if data_sub:
            _, extra_subs = languages_from_tags(f"[SUB-{data_sub.upper()}]")
            sub_langs = list(dict.fromkeys(sub_langs + extra_subs))

        source_url = urljoin(base_url, href)
        if source_url in seen:
            continue
        seen.add(source_url)

        title_id = re.search(r"/(\d+)-", href)
        items.append(
            ScrapedItem(
                title=title,
                provider_slug=provider_slug,
                year=year,
                content_type=content_type,
                source_url=source_url,
                audio_languages=audio_langs,
                subtitle_languages=sub_langs,
                # Listing cards identify titles; they do not verify playable
                # streams or resolution. Keep this as an unverified discovery.
                quality=None,
                availability=True,
                raw_payload={
                    "url": source_url,
                    "title_id": title_id.group(1) if title_id else None,
                    "domain": urlparse(base_url).netloc,
                    "provenance": f"{provider_slug}_{source_label}",
                    # A listing card proves that the title was discovered,
                    # not that playback, territorial access, or quality was
                    # verified. The ingestion bridge keeps this unmapped until
                    # a reviewer confirms it, so it cannot become an offer.
                    "unverified_discovery": True,
                },
            )
        )

    return items
