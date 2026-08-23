"""Provider configuration schemas, dataclasses, and Python source-of-truth default registry."""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List, Optional


@dataclass
class SelectorConfig:
    container: Optional[str] = None
    title: Optional[str] = None
    year: Optional[str] = None
    url: Optional[str] = None
    content_type: Optional[str] = None
    season: Optional[str] = None
    episode: Optional[str] = None
    episode_title: Optional[str] = None
    audio_languages: Optional[str] = None
    subtitle_languages: Optional[str] = None
    quality: Optional[str] = None
    hdr: Optional[str] = None
    pagination_next: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class CrawlLimits:
    max_pages: int = 5
    max_items: int = 50
    max_depth: int = 2
    timeout: int = 10
    max_retries: int = 2

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class ProviderConfig:
    provider_slug: str
    domain: str
    search_url_template: str  # e.g., "https://example.com/search?q={query}"
    response_type: str = "html"  # "html" | "json" | "auto"
    json_results_path: Optional[str] = None  # e.g. "results" or "data.items"
    selectors: SelectorConfig = field(default_factory=SelectorConfig)
    limits: CrawlLimits = field(default_factory=CrawlLimits)
    detail_url_patterns: List[str] = field(default_factory=list)
    custom_headers: Dict[str, str] = field(default_factory=dict)
    language_map: Dict[str, str] = field(default_factory=dict)
    fallback_domains: List[str] = field(default_factory=list)
    default_audio_languages: List[str] = field(default_factory=list)
    default_subtitle_languages: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


# Default configurations for known providers
DEFAULT_PROVIDER_CONFIGS: Dict[str, ProviderConfig] = {
    "kisskh": ProviderConfig(
        provider_slug="kisskh",
        domain="kisskh.co",
        search_url_template="https://kisskh.co/api/DramaList/Search?q={query}",
        response_type="json",
        json_results_path="",  # top-level list
        limits=CrawlLimits(max_pages=3, max_items=50, max_depth=1),
        detail_url_patterns=[r"^https://kisskh\.co/Drama/\d+"],
        default_audio_languages=["EN"],
        default_subtitle_languages=["EN"],
    ),
    "hydrahd": ProviderConfig(
        provider_slug="hydrahd",
        domain="hydrahd.ws",
        search_url_template="https://hydrahd.ws/api/search?q={query}",
        response_type="json",
        json_results_path="results",
        fallback_domains=["hydrahd.ru"],
        limits=CrawlLimits(max_pages=3, max_items=50, max_depth=1),
        custom_headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "en-US,en;q=0.9",
            "Referer": "https://hydrahd.ws/",
        },
        default_audio_languages=["EN"],
        default_subtitle_languages=[],
    ),
    "animeworld": ProviderConfig(
        provider_slug="animeworld",
        domain="animeworld.ac",
        search_url_template="https://animeworld.ac/api/search?keyword={query}",
        response_type="html",
        json_results_path="html",
        selectors=SelectorConfig(
            container=".item, div.anime, div.entry, div.col-",
            title="a.name, .title a, a[href*='/play/'], a.film-name",
            year=".info, p, span.year",
            url="a.name, .title a, a[href*='/play/']",
        ),
        limits=CrawlLimits(max_pages=5, max_items=100, max_depth=2),
        detail_url_patterns=[r"^https://animeworld\.ac/play/"],
        default_audio_languages=["JA"],
        default_subtitle_languages=["IT", "EN"],
    ),
    "generic": ProviderConfig(
        provider_slug="generic",
        domain="localhost",
        search_url_template="https://{domain}/search?q={query}",
        response_type="auto",
        selectors=SelectorConfig(
            container=".search-result, .card, .item, .movie-item, .entry, tr.result",
            title=".title, .name, h2 a, h3 a, a.title",
            year=".year, .release-date, span.date",
            url="a.title, h2 a, h3 a, a[href]",
        ),
        limits=CrawlLimits(max_pages=3, max_items=30, max_depth=1),
    ),
}


def merge_provider_config(
    base_config: ProviderConfig, db_overrides: Optional[Dict[str, Any]] = None
) -> ProviderConfig:
    """Merge Python base config with valid database JSON config overrides."""
    if not db_overrides or not isinstance(db_overrides, dict):
        return base_config

    cfg_dict = base_config.to_dict()

    for key, val in db_overrides.items():
        if val is None:
            continue
        if key == "selectors" and isinstance(val, dict):
            for sel_key, sel_val in val.items():
                if sel_val is not None and sel_key in cfg_dict["selectors"]:
                    cfg_dict["selectors"][sel_key] = str(sel_val)
        elif key == "limits" and isinstance(val, dict):
            for lim_key, lim_val in val.items():
                if lim_val is not None and lim_key in cfg_dict["limits"]:
                    try:
                        cfg_dict["limits"][lim_key] = int(lim_val)
                    except (ValueError, TypeError):
                        pass
        elif key in cfg_dict:
            cfg_dict[key] = val

    selectors = SelectorConfig(**cfg_dict.get("selectors", {}))
    limits = CrawlLimits(**cfg_dict.get("limits", {}))

    return ProviderConfig(
        provider_slug=str(cfg_dict.get("provider_slug", base_config.provider_slug)),
        domain=str(cfg_dict.get("domain", base_config.domain)),
        search_url_template=str(cfg_dict.get("search_url_template", base_config.search_url_template)),
        response_type=str(cfg_dict.get("response_type", base_config.response_type)),
        json_results_path=cfg_dict.get("json_results_path"),
        selectors=selectors,
        limits=limits,
        detail_url_patterns=list(cfg_dict.get("detail_url_patterns", [])),
        custom_headers=dict(cfg_dict.get("custom_headers", {})),
        language_map=dict(cfg_dict.get("language_map", {})),
        fallback_domains=list(cfg_dict.get("fallback_domains", [])),
        default_audio_languages=list(cfg_dict.get("default_audio_languages", [])),
        default_subtitle_languages=list(cfg_dict.get("default_subtitle_languages", [])),
    )


def validate_provider_config(db_config: Any) -> tuple[bool, Optional[str]]:
    """Validate DB configuration dictionary against expected schema."""
    if db_config is None:
        return True, None
    if not isinstance(db_config, dict):
        return False, "Provider config must be a dictionary object"

    allowed_keys = set(ProviderConfig(provider_slug="", domain="", search_url_template="").to_dict().keys())
    unknown_keys = set(db_config.keys()) - allowed_keys
    if unknown_keys:
        return False, f"Unknown top-level config keys: {', '.join(unknown_keys)}"

    if "limits" in db_config and isinstance(db_config["limits"], dict):
        allowed_limits = set(CrawlLimits().to_dict().keys())
        unknown_limits = set(db_config["limits"].keys()) - allowed_limits
        if unknown_limits:
            return False, f"Unknown limit keys: {', '.join(unknown_limits)}"

    if "selectors" in db_config and isinstance(db_config["selectors"], dict):
        allowed_selectors = set(SelectorConfig().to_dict().keys())
        unknown_selectors = set(db_config["selectors"].keys()) - allowed_selectors
        if unknown_selectors:
            return False, f"Unknown selector keys: {', '.join(unknown_selectors)}"

    return True, None
