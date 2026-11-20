"""Restore missing official provider metadata without changing existing rows.

Replays the canonical provider inserts from migrations/commands:
- migration 0007 (MAJOR_PROVIDERS + initial SCRAPER_PROVIDERS)
- migration 0011 (FREE_PROVIDERS: European public broadcasters & global free)
- migration 0014 (move bbc_iplayer -> free)
- migration 0018 (NHK -> free)
- migration 0032 (youtube -> free)
- migration 0040 (rakuten_tv -> avod, JustWatch ``wuaki``)

The alias providers from migration 0012 (rai-play, bbc, nhk) are deliberately
NOT replayed: they were duplicates of raiplay / bbc_iplayer / nhk_world and
were merged away by migration 0033, which also makes sharing a domain root
between two provider rows impossible.

The separate seed_unofficial_providers command owns unofficial metadata.
Provider writes run only on explicit command invocation.
"""
from django.core.management.base import BaseCommand
from django.db import transaction

from tracker.models import Provider, ProviderCategory, ExternalProviderMapping

MAJOR_PROVIDERS = [
    {"slug": "prime", "name": "Prime Video", "icon": "prime", "sa_id": "prime"},
    {"slug": "netflix", "name": "Netflix", "icon": "netflix", "sa_id": "netflix"},
    {"slug": "disney", "name": "Disney+", "icon": "disney", "sa_id": "disney"},
    {"slug": "hulu", "name": "Hulu", "icon": "hulu", "sa_id": "hulu"},
    {"slug": "max", "name": "Max", "icon": "max", "sa_id": "hbo"},
    {"slug": "apple", "name": "Apple TV+", "icon": "apple", "sa_id": "apple"},
    {"slug": "paramount", "name": "Paramount+", "icon": "paramount", "sa_id": "paramount"},
    {"slug": "peacock", "name": "Peacock", "icon": "peacock", "sa_id": "peacock"},
    {"slug": "youtubetv", "name": "YouTube TV", "icon": "youtubetv", "sa_id": "youtubetv"},
    {"slug": "crunchyroll", "name": "Crunchyroll", "icon": "crunchyroll", "sa_id": "crunchyroll"},
]

FREE_PROVIDERS = [
    {"slug": "raiplay", "name": "RaiPlay", "icon": "raiplay", "domain": "raiplay.it", "country": "IT"},
    {"slug": "france_tv", "name": "France.tv", "icon": "france_tv", "domain": "france.tv", "country": "FR"},
    {"slug": "ard_zdf", "name": "ARD Mediathek / ZDF", "icon": "ard_zdf", "domain": "ardmediathek.de", "country": "DE"},
    {"slug": "rtve_play", "name": "RTVE Play", "icon": "rtve_play", "domain": "rtve.es", "country": "ES"},
    {"slug": "npo_start", "name": "NPO Start", "icon": "npo_start", "domain": "npostart.nl", "country": "NL"},
    {"slug": "vrt_auvio", "name": "VRT MAX / Auvio", "icon": "vrt_auvio", "domain": "vrt.be", "country": "BE"},
    {"slug": "rtp_play", "name": "RTP Play", "icon": "rtp_play", "domain": "rtp.pt", "country": "PT"},
    {"slug": "svt_play", "name": "SVT Play", "icon": "svt_play", "domain": "svtplay.se", "country": "SE"},
    {"slug": "nrk_tv", "name": "NRK TV", "icon": "nrk_tv", "domain": "tv.nrk.no", "country": "NO"},
    {"slug": "drtv", "name": "DRTV", "icon": "drtv", "domain": "dr.dk", "country": "DK"},
    {"slug": "yle_areena", "name": "Yle Areena", "icon": "yle_areena", "domain": "areena.yle.fi", "country": "FI"},
    {"slug": "orf_on", "name": "ORF ON", "icon": "orf_on", "domain": "on.orf.at", "country": "AT"},
    {"slug": "play_suisse", "name": "Play Suisse", "icon": "play_suisse", "domain": "playsuisse.ch", "country": "CH"},
    {"slug": "ertflix", "name": "ERTFLIX", "icon": "ertflix", "domain": "ertflix.gr", "country": "GR"},
    {"slug": "rte_player", "name": "RTÉ Player", "icon": "rte_player", "domain": "rte.ie", "country": "IE"},
    {"slug": "bbc_iplayer", "name": "BBC iPlayer", "icon": "bbc_iplayer", "domain": "bbc.co.uk/iplayer", "country": "GB"},
    {"slug": "ivysilani", "name": "iVysílání", "icon": "ivysilani", "domain": "ceskatelevize.cz", "country": "CZ"},
    {"slug": "tvp_vod", "name": "TVP VOD", "icon": "tvp_vod", "domain": "vod.tvp.pl", "country": "PL"},
    {"slug": "mediaklikk", "name": "MédiaKlikk", "icon": "mediaklikk", "domain": "mediaklikk.hu", "country": "HU"},
    {"slug": "hrti", "name": "HRTi", "icon": "hrti", "domain": "hrti.hrt.hr", "country": "HR"},
    {"slug": "pbs", "name": "PBS", "icon": "pbs", "domain": "pbs.org", "country": "US"},
    {"slug": "abc_iview", "name": "ABC iview", "icon": "abc_iview", "domain": "iview.abc.net.au", "country": "AU"},
    {"slug": "mediaset_infinity", "name": "Mediaset Infinity", "icon": "mediaset_infinity", "domain": "mediasetinfinity.mediaset.it", "country": "IT"},
    {"slug": "sbs_ondemand", "name": "SBS On Demand", "icon": "sbs_ondemand", "domain": "sbs.com.au", "country": "AU"},
    {"slug": "cbc_gem", "name": "CBC Gem", "icon": "cbc_gem", "domain": "gem.cbc.ca", "country": "CA"},
    {"slug": "tubi", "name": "Tubi", "icon": "tubi", "domain": "tubitv.com", "country": "US"},
    {"slug": "pluto_tv", "name": "Pluto TV", "icon": "pluto_tv", "domain": "pluto.tv", "country": "US"},
    {"slug": "canal_once", "name": "Canal Once", "icon": "canal_once", "domain": "canalonce.mx", "country": "MX"},
    {"slug": "tv_brasil", "name": "TV Brasil", "icon": "tv_brasil", "domain": "tvbrasil.ebc.com.br", "country": "BR"},
    {"slug": "tv_publica", "name": "TV Pública", "icon": "tv_publica", "domain": "tvpublica.com.ar", "country": "AR"},
    {"slug": "kbs", "name": "KBS", "icon": "kbs", "domain": "kbs.co.kr", "country": "KR"},
    {"slug": "nhk_world", "name": "NHK WORLD-JAPAN", "icon": "nhk_world", "domain": "nhk.or.jp/nhkworld", "country": "JP"},
    {"slug": "youtube", "name": "YouTube", "icon": "youtube", "domain": "youtube.com", "country": ""},
]

AVOD_PROVIDERS = [
    {"slug": "rakuten_tv", "name": "Rakuten TV", "icon": "rakuten_tv", "domain": "rakuten.tv", "country": ""},
]


def upsert(slug, defaults):
    obj, _ = Provider.objects.get_or_create(slug=slug, defaults=defaults)
    return obj


class Command(BaseCommand):
    help = "Restore missing official provider metadata and Streaming Availability mappings."

    @transaction.atomic
    def handle(self, *args, **options):
        for m in MAJOR_PROVIDERS:
            p = upsert(m["slug"], {
                "name": m["name"],
                "category": ProviderCategory.OFFICIAL,
                "is_major": True,
                "is_enabled": True,
                "icon_name": m["icon"],
            })
            ExternalProviderMapping.objects.get_or_create(
                provider=p,
                integration_name="streaming_availability",
                defaults={"external_id": m["sa_id"]},
            )

        for f in FREE_PROVIDERS:
            upsert(f["slug"], {
                "name": f["name"],
                "category": ProviderCategory.BVOD,
                "domain": f["domain"],
                "country_code": f["country"],
            })

        for a in AVOD_PROVIDERS:
            upsert(a["slug"], {
                "name": a["name"],
                "category": ProviderCategory.AVOD,
                "is_major": False,
                "is_enabled": True,
                "icon_name": a["icon"],
                "domain": a["domain"],
                "country_code": a["country"],
            })

        self.stdout.write(f"total_providers {Provider.objects.count()}")
        for p in Provider.objects.order_by("category", "id"):
            self.stdout.write(f"{p.id:3d} {p.category:10s} major={int(p.is_major)} enabled={int(p.is_enabled)} {p.slug:14s} {p.name}")
