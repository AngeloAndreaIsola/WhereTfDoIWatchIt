from __future__ import annotations

import logging
import re
from typing import Any, Optional
from urllib.parse import urlparse

from tracker.scrapers.base import BaseScraper, ScrapedItem

logger = logging.getLogger(__name__)

OKRU_HOSTS = ("ok.ru", "www.ok.ru")
VIDEO_ID_RE = re.compile(r"/video/(\d+)")


class OKRuScraper(BaseScraper):
    provider_slug = "ok_ru"
    timeout = 10

    def _execute_run(self, query: Optional[str] = None) -> list[ScrapedItem]:
        if not query:
            return []
        url = query.strip()
        if not self._is_safe_okru_url(url):
            logger.warning("OK.ru: refusing unsafe/non-okru URL %s", url)
            return []
        raw = self.fetch_url(url)
        if not raw or not isinstance(raw, str):
            return []
        return self.parse_html(raw, url)

    @staticmethod
    def _is_safe_okru_url(url: str) -> bool:
        try:
            p = urlparse(url)
        except Exception:
            return False
        if p.scheme not in ("http", "https"):
            return False
        if p.netloc.lower() not in OKRU_HOSTS:
            return False
        return bool(VIDEO_ID_RE.search(p.path))

    @staticmethod
    def extract_video_id(url: str) -> Optional[str]:
        m = VIDEO_ID_RE.search(url or "")
        return m.group(1) if m else None

    def parse_html(self, html: str, source_url: str) -> list[ScrapedItem]:
        items: list[ScrapedItem] = []
        video_id = self.extract_video_id(source_url)

        title = None
        m = re.search(r'<meta\s+name="title"\s+content="([^"]*)"', html)
        if m:
            title = m.group(1)
        if not title:
            m = re.search(r'<meta\s+property="og:title"\s+content="([^"]*)"', html)
            if m:
                title = m.group(1)
        if not title:
            return items

        desc = None
        m = re.search(r'<meta\s+name="description"\s+content="([^"]*)"', html)
        if m:
            desc = m.group(1)
        if not desc:
            m = re.search(r'<meta\s+property="og:description"\s+content="([^"]*)"', html)
            if m:
                desc = m.group(1)

        thumb = None
        m = re.search(r'<meta\s+property="og:image"\s+content="([^"]*)"', html)
        if m:
            thumb = m.group(1)

        duration = None
        m = re.search(r'<meta\s+property="og:video:duration"\s+content="([^"]*)"', html)
        if m:
            duration = m.group(1)

        year = None
        ym = re.search(r"\((\d{4})\)", title)
        if ym:
            try:
                year = int(ym.group(1))
            except ValueError:
                year = None

        items.append(ScrapedItem(
            title=title,
            provider_slug=self.provider_slug,
            original_title=None,
            year=year,
            content_type="movie",
            source_url=source_url,
            audio_languages=[],
            subtitle_languages=[],
            quality=None,
            availability=True,
            raw_payload={
                "video_id": video_id,
                "description": desc,
                "thumbnail": thumb,
                "duration_seconds": duration,
                "provenance": "user_upload_unverified",
                "unverified_discovery": True,
            },
        ))
        return items
