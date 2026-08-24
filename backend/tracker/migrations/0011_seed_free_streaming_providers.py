# Migration for seeding free streaming providers (European public broadcasters & global free services)

from django.db import migrations


FREE_PROVIDERS = [
    # European public broadcasters
    {
        "slug": "raiplay",
        "name": "RaiPlay",
        "icon_name": "raiplay",
        "domain": "raiplay.it",
    },
    {
        "slug": "france_tv",
        "name": "France.tv",
        "icon_name": "france_tv",
        "domain": "france.tv",
    },
    {
        "slug": "ard_zdf",
        "name": "ARD Mediathek / ZDF",
        "icon_name": "ard_zdf",
        "domain": "ardmediathek.de",
    },
    {
        "slug": "rtve_play",
        "name": "RTVE Play",
        "icon_name": "rtve_play",
        "domain": "rtve.es",
    },
    {
        "slug": "npo_start",
        "name": "NPO Start",
        "icon_name": "npo_start",
        "domain": "npostart.nl",
    },
    {
        "slug": "vrt_auvio",
        "name": "VRT MAX / Auvio",
        "icon_name": "vrt_auvio",
        "domain": "vrt.be",
    },
    {
        "slug": "rtp_play",
        "name": "RTP Play",
        "icon_name": "rtp_play",
        "domain": "rtp.pt",
    },
    {
        "slug": "svt_play",
        "name": "SVT Play",
        "icon_name": "svt_play",
        "domain": "svtplay.se",
    },
    {
        "slug": "nrk_tv",
        "name": "NRK TV",
        "icon_name": "nrk_tv",
        "domain": "tv.nrk.no",
    },
    {
        "slug": "drtv",
        "name": "DRTV",
        "icon_name": "drtv",
        "domain": "dr.dk",
    },
    {
        "slug": "yle_areena",
        "name": "Yle Areena",
        "icon_name": "yle_areena",
        "domain": "areena.yle.fi",
    },
    {
        "slug": "orf_on",
        "name": "ORF ON",
        "icon_name": "orf_on",
        "domain": "on.orf.at",
    },
    {
        "slug": "play_suisse",
        "name": "Play Suisse",
        "icon_name": "play_suisse",
        "domain": "playsuisse.ch",
    },
    {
        "slug": "ertflix",
        "name": "ERTFLIX",
        "icon_name": "ertflix",
        "domain": "ertflix.gr",
    },
    {
        "slug": "rte_player",
        "name": "RTÉ Player",
        "icon_name": "rte_player",
        "domain": "rte.ie",
    },
    {
        "slug": "bbc_iplayer",
        "name": "BBC iPlayer",
        "icon_name": "bbc_iplayer",
        "domain": "bbc.co.uk/iplayer",
    },
    {
        "slug": "ivysilani",
        "name": "iVysílání",
        "icon_name": "ivysilani",
        "domain": "ceskatelevize.cz",
    },
    {
        "slug": "tvp_vod",
        "name": "TVP VOD",
        "icon_name": "tvp_vod",
        "domain": "vod.tvp.pl",
    },
    {
        "slug": "mediaklikk",
        "name": "MédiaKlikk",
        "icon_name": "mediaklikk",
        "domain": "mediaklikk.hu",
    },
    {
        "slug": "hrti",
        "name": "HRTi",
        "icon_name": "hrti",
        "domain": "hrti.hrt.hr",
    },

    # Outside Europe
    {
        "slug": "sbs_ondemand",
        "name": "SBS On Demand",
        "icon_name": "sbs_ondemand",
        "domain": "sbs.com.au",
    },
    {
        "slug": "cbc_gem",
        "name": "CBC Gem",
        "icon_name": "cbc_gem",
        "domain": "gem.cbc.ca",
    },
    {
        "slug": "tubi",
        "name": "Tubi",
        "icon_name": "tubi",
        "domain": "tubitv.com",
    },
    {
        "slug": "pluto_tv",
        "name": "Pluto TV",
        "icon_name": "pluto_tv",
        "domain": "pluto.tv",
    },
    {
        "slug": "canal_once",
        "name": "Canal Once",
        "icon_name": "canal_once",
        "domain": "canalonce.mx",
    },
    {
        "slug": "tv_brasil",
        "name": "TV Brasil",
        "icon_name": "tv_brasil",
        "domain": "tvbrasil.ebc.com.br",
    },
    {
        "slug": "tv_publica",
        "name": "TV Pública",
        "icon_name": "tv_publica",
        "domain": "tvpublica.com.ar",
    },
    {
        "slug": "kbs",
        "name": "KBS",
        "icon_name": "kbs",
        "domain": "kbs.co.kr",
    },
    {
        "slug": "nhk_world",
        "name": "NHK WORLD-JAPAN",
        "icon_name": "nhk_world",
        "domain": "nhk.or.jp/nhkworld",
    },
]


def seed_free_providers(apps, schema_editor):
    Provider = apps.get_model("tracker", "Provider")

    for item in FREE_PROVIDERS:
        Provider.objects.get_or_create(
            slug=item["slug"],
            defaults={
                "name": item["name"],
                "category": "free",
                "is_major": False,
                "is_enabled": True,
                "icon_name": item["icon_name"],
                "domain": item["domain"],
            },
        )


def reverse_seed_free_providers(apps, schema_editor):
    Provider = apps.get_model("tracker", "Provider")
    slugs = [item["slug"] for item in FREE_PROVIDERS]
    Provider.objects.filter(slug__in=slugs, category="free").delete()


class Migration(migrations.Migration):

    dependencies = [
        ("tracker", "0010_preference_language_rule_and_more"),
    ]

    operations = [
        migrations.RunPython(seed_free_providers, reverse_seed_free_providers),
    ]
