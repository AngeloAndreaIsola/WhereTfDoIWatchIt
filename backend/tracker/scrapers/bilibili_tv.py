from __future__ import annotations

import json
import logging
import re
from typing import Any, Optional
from urllib.parse import urlparse

from tracker.scrapers.base import BaseScraper, ScrapedItem

logger = logging.getLogger(__name__)

BILIBILI_TV_HOST = "www.bilibili.tv"
VIDEO_ID_RE = re.compile(r"/video/(\d+)")


class BilibiliTVScraper(BaseScraper):
    provider_slug = "bilibili_tv"
    timeout = 10

    def _execute_run(self, query: Optional[str] = None) -> list[ScrapedItem]:
        if not query:
            return []
        url = query.strip()
        if not self._is_safe_bilibili_url(url):
            logger.warning("BilibiliTV: refusing unsafe/non-bilibili URL %s", url)
            return []
        raw = self.fetch_url(url)
        if not raw or not isinstance(raw, str):
            return []
        return self.parse_html(raw, url)

    @staticmethod
    def _is_safe_bilibili_url(url: str) -> bool:
        try:
            p = urlparse(url)
        except Exception:
            return False
        if p.scheme not in ("http", "https"):
            return False
        if p.netloc.lower() not in (BILIBILI_TV_HOST, "bilibili.tv"):
            return False
        return bool(VIDEO_ID_RE.search(p.path))

    @staticmethod
    def extract_video_id(url: str) -> Optional[str]:
        m = VIDEO_ID_RE.search(url or "")
        return m.group(1) if m else None

    def parse_html(self, html: str, source_url: str) -> list[ScrapedItem]:
        items: list[ScrapedItem] = []
        video_id = self.extract_video_id(source_url)
        for m in re.finditer(r'<script[^>]*type="application/ld\+json"[^>]*>(.*?)</script>', html, re.S):
            try:
                data = json.loads(m.group(1))
            except (json.JSONDecodeError, ValueError):
                continue
            candidates = data if isinstance(data, list) else [data]
            for c in candidates:
                if not isinstance(c, dict) or c.get("@type") != "VideoObject":
                    continue
                title = c.get("name") or c.get("headline")
                if not title:
                    continue
                items.append(ScrapedItem(
                    title=str(title),
                    provider_slug=self.provider_slug,
                    original_title=None,
                    year=None,
                    content_type="movie",
                    source_url=source_url,
                    audio_languages=[],
                    subtitle_languages=[],
                    quality=None,
                    availability=True,
                    raw_payload={
                        "video_id": video_id,
                        "description": c.get("description"),
                        "thumbnail": c.get("thumbnailUrl"),
                        "upload_date": c.get("uploadDate"),
                        "duration": c.get("duration"),
                        "provenance": "user_upload_unverified",
                        "unverified_discovery": True,
                    },
                ))
        return items
