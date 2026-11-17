# Generated manually for PR #47 cache hardening.
from django.db import migrations, models
from availability.models import tmdb_default_cache_expiry


class Migration(migrations.Migration):
    dependencies = [
        ("availability", "0012_tmdbsearchcache_providercontentcandidate"),
    ]

    operations = [
        migrations.AddField(
            model_name="tmdbsearchcache",
            name="endpoint_version",
            field=models.CharField(default="v3", max_length=16),
        ),
        migrations.AddField(
            model_name="tmdbsearchcache",
            name="expires_at",
            field=models.DateTimeField(db_index=True, default=tmdb_default_cache_expiry),
        ),
        migrations.AddField(
            model_name="tmdbsearchcache",
            name="locale",
            field=models.CharField(default="en-US", max_length=32),
        ),
        migrations.AddField(
            model_name="tmdbsearchcache",
            name="year_fingerprint",
            field=models.CharField(default="year:none", editable=False, max_length=16),
        ),
        migrations.RemoveConstraint(
            model_name="tmdbsearchcache",
            name="uniq_tmdbsearchcache_query_year_type",
        ),
        migrations.AddConstraint(
            model_name="tmdbsearchcache",
            constraint=models.UniqueConstraint(
                fields=("query", "year_fingerprint", "content_type", "locale", "endpoint_version"),
                name="uniq_tmdbsearchcache_identity",
            ),
        ),
        migrations.AddIndex(
            model_name="tmdbsearchcache",
            index=models.Index(fields=["expires_at"], name="idx_tmdbsearchcache_expiry"),
        ),
    ]
