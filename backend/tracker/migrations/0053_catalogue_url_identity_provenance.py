from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from django.db import migrations, models


def _identity(url):
    p = urlsplit((url or "").strip())
    if p.scheme.lower() not in {"http", "https"} or not p.hostname:
        return url or ""
    host = p.hostname.rstrip(".").lower()
    port = p.port
    if port and not ((p.scheme.lower() == "https" and port == 443) or (p.scheme.lower() == "http" and port == 80)):
        host = f"{host}:{port}"
    query = [(k, v) for k, v in parse_qsl(p.query, keep_blank_values=True)
             if k.lower() not in {"fbclid", "gclid", "dclid", "mc_cid", "mc_eid"}
             and not k.lower().startswith("utm_")]
    return urlunsplit((p.scheme.lower(), host, p.path or "/", urlencode(query, doseq=True), ""))


def backfill(apps, schema_editor):
    Record = apps.get_model("tracker", "DiscoveredCatalogueRecord")
    Provider = apps.get_model("tracker", "Provider")
    for row in Record.objects.select_related("provider").order_by("id"):
        row.identity_url = _identity(row.source_url)
        row.source_urls_seen = [row.source_url]
        if row.provider.slug == "animeworld":
            row.provenance_state, row.access_policy_state = "VERIFIED", "RESTRICTED"
        elif row.provider.slug == "hydrahd":
            row.provenance_state, row.access_policy_state = "SELF_ASSERTED", "PERMITTED"
        row.save(update_fields=["identity_url", "source_urls_seen", "provenance_state", "access_policy_state"])

    # Merge only exact normalized URL collisions; preserve each original URL in
    # the surviving observation. Titles are never an identity key.
    seen = {}
    for row in Record.objects.filter(provider_native_id="").order_by("id"):
        key = (row.provider_id, row.identity_url)
        if not row.identity_url or key not in seen:
            seen[key] = row
            continue
        keep = seen[key]
        urls = list(dict.fromkeys((keep.source_urls_seen or []) + (row.source_urls_seen or []) + [row.source_url]))
        keep.source_urls_seen = urls
        keep.save(update_fields=["source_urls_seen"])
        row.delete()


class Migration(migrations.Migration):
    dependencies = [("tracker", "0052_discovered_catalogue_record")]

    operations = [
        migrations.AddField("discoveredcataloguerecord", "identity_url", models.URLField(blank=True, default="", max_length=500)),
        migrations.AddField("discoveredcataloguerecord", "source_urls_seen", models.JSONField(blank=True, default=list)),
        migrations.AddField("discoveredcataloguerecord", "provenance_state", models.CharField(default="PROVENANCE_UNVERIFIED", max_length=24)),
        migrations.AddField("discoveredcataloguerecord", "access_policy_state", models.CharField(default="UNKNOWN", max_length=16)),
        migrations.RemoveConstraint("discoveredcataloguerecord", "uniq_discovered_provider_source_url"),
        migrations.RunPython(backfill, migrations.RunPython.noop),
        migrations.AddConstraint("discoveredcataloguerecord", models.UniqueConstraint(condition=models.Q(("provider_native_id", ""), models.Q(("identity_url", ""), _negated=True)), fields=("provider", "identity_url"), name="uniq_discovered_provider_identity_url")),
    ]
