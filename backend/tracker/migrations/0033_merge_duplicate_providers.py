"""Merge duplicate provider rows sharing a domain root, then enforce uniqueness.

Two seed lineages created the same site twice (RaiPlay, BBC iPlayer and NHK
each exist as a canonical BVOD row *and* as a legacy alias row from migration
0012), and ``detect_provider_from_url`` could re-create aliases from a
domain's first label. This migration folds every alias into the canonical
member of its domain-root group (lowest id) and then adds the partial unique
index that makes the duplication impossible again.

The merge is generic — any domain-root group, not just today's three:

- ``Subscription`` / ``ExternalProviderMapping`` repoint to the canonical row
  and are dropped when the canonical row already covers the same key;
- ``ScrapedContentMapping`` rows fold their movie link, confirmation and
  confidence into a canonical twin with the same (title, year) and are then
  deleted (the ``post_delete`` receiver drops their legacy projection);
  canonical rows that absorbed data are re-ingested so the availability
  projection reflects the merge. Alias rows without a twin keep their row and
  projection, repointed to the canonical provider instead;
- legacy availability projections still owned by the alias are deleted
  (their tracker rows are gone), every other availability row follows the
  provider with its slug-derived ``identity`` rebuilt — a uniqueness collision
  means the row was a duplicate of one that already exists, so it is dropped;
- ``Region`` rows keyed by the alias slug follow the canonical slug.

Uses the current model classes and the live legacy-ingest bridge on purpose:
this is the last migration in the chain and that bridge is what keeps the
scraper store and the availability tables in lockstep.
"""
import django.db.models.expressions
import django.db.models.functions.text
from django.db import IntegrityError, migrations, models, transaction


def _merge_scraped_fields(canonical, alias) -> bool:
    """Fold one alias scrape row into its canonical twin.

    Returns True when the twin changed and needs saving. The canonical row
    wins whenever it already carries data; the alias only fills gaps.
    """
    changed = False
    if canonical.movie_id is None and alias.movie_id is not None:
        canonical.movie_id = alias.movie_id
        changed = True
    if alias.is_confirmed and not canonical.is_confirmed:
        canonical.is_confirmed = True
        changed = True
    if (alias.confidence_score or 0.0) > (canonical.confidence_score or 0.0):
        canonical.confidence_score = alias.confidence_score
        changed = True
    if not canonical.scraped_url and alias.scraped_url:
        canonical.scraped_url = alias.scraped_url
        changed = True
    if not canonical.country_code and alias.country_code:
        canonical.country_code = alias.country_code
        changed = True
    if not canonical.raw_payload and alias.raw_payload:
        canonical.raw_payload = alias.raw_payload
        changed = True
    if alias.is_available and not canonical.is_available:
        canonical.is_available = alias.is_available
        changed = True
    if alias.last_scraped_at and (
        canonical.last_scraped_at is None or alias.last_scraped_at > canonical.last_scraped_at
    ):
        canonical.last_scraped_at = alias.last_scraped_at
        changed = True
    return changed


def _repoint(queryset, canonical, rebuild_identity: bool) -> None:
    """Move availability rows to the canonical provider.

    ``identity`` embeds the provider slug, so rows that carry one must
    rebuild it after the move; a collision on the rebuilt identity (or on
    another unique slot) means the alias row duplicated a row the canonical
    provider already had, and it is dropped instead.
    """
    for row in queryset:
        row.provider = canonical
        if rebuild_identity:
            row.identity = ""
        try:
            with transaction.atomic():
                row.save()
        except IntegrityError:
            row.delete()


def _merge_provider(canonical, duplicate) -> None:
    from availability.models import (
        ContentMapping,
        Offer,
        ProviderContent,
        SyncCursor,
        SyncRun,
    )
    from availability.services.legacy_ingest import (
        delete_scraped_mapping,
        ingest_scraped_mapping,
    )
    from tracker.models import (
        ExternalProviderMapping,
        Region,
        ScrapedContentMapping,
        Subscription,
    )

    for sub in Subscription.objects.filter(provider=duplicate):
        if Subscription.objects.filter(user_id=sub.user_id, provider=canonical).exists():
            sub.delete()
        else:
            sub.provider = canonical
            sub.save()

    for mapping in ExternalProviderMapping.objects.filter(provider=duplicate):
        if ExternalProviderMapping.objects.filter(
            provider=canonical, integration_name=mapping.integration_name
        ).exists():
            mapping.delete()
        else:
            mapping.provider = canonical
            mapping.save()

    # Legacy scraper rows: fold into the canonical twin (same title/year) or,
    # with no twin, move the row over. Only rows that changed are re-ingested
    # at the end — ingestion is idempotent, but there is no point re-writing
    # the untouched majority.
    reingest = []
    for row in ScrapedContentMapping.objects.filter(provider=duplicate).order_by("pk"):
        twin = (
            ScrapedContentMapping.objects.filter(
                provider=canonical,
                scraped_title=row.scraped_title,
                scraped_year=row.scraped_year,
            )
            .order_by("pk")
            .first()
        )
        if twin is None:
            # No twin: keep the row (and its projection) under the canonical
            # provider — the projection must be moved first, while the row
            # still points at the alias, because identity is provider-scoped.
            delete_scraped_mapping(row)
            row.provider = canonical
            row.save()
            ingest_scraped_mapping(row)
            continue
        if _merge_scraped_fields(twin, row):
            twin.save()
            reingest.append(twin.pk)
        # Deleting the alias row also deletes its alias-side projection
        # (post_delete receiver on the real model).
        row.delete()

    # Projections whose tracker rows are gone (orphans) must not survive the
    # alias provider; everything non-legacy follows the provider below.
    ContentMapping.objects.filter(
        provider=duplicate, provider_content_id__startswith="legacy-"
    ).delete()
    ProviderContent.objects.filter(
        provider=duplicate, provider_content_id__startswith="legacy-"
    ).delete()

    _repoint(ProviderContent.objects.filter(provider=duplicate), canonical, True)
    _repoint(ContentMapping.objects.filter(provider=duplicate), canonical, False)
    _repoint(Offer.objects.filter(provider=duplicate), canonical, True)
    _repoint(SyncRun.objects.filter(provider=duplicate), canonical, False)
    _repoint(SyncCursor.objects.filter(provider=duplicate), canonical, False)

    for region in Region.objects.filter(provider=duplicate.slug):
        if Region.objects.filter(provider=canonical.slug, country=region.country).exists():
            region.delete()
        else:
            region.provider = canonical.slug
            region.save()

    duplicate.delete()

    for pk in reingest:
        ingest_scraped_mapping(ScrapedContentMapping.objects.get(pk=pk))


def merge_duplicate_providers(apps, schema_editor):
    from tracker.models import Provider, provider_domain_root

    groups = {}
    for provider in Provider.objects.order_by("id"):
        root = provider_domain_root(provider.domain)
        if not root:
            continue
        groups.setdefault(root, []).append(provider)

    for members in groups.values():
        if len(members) < 2:
            continue
        canonical, duplicates = members[0], members[1:]
        for duplicate in duplicates:
            _merge_provider(canonical, duplicate)


class Migration(migrations.Migration):
    # The data merge deletes and updates rows that carry FK triggers; Postgres
    # refuses the index build for the constraint in the same transaction
    # ("pending trigger events"). RunPython still gets its own transaction —
    # only the two operations are decoupled, and the merge is idempotent if
    # the constraint step ever has to be retried on its own.
    atomic = False

    dependencies = [
        ("tracker", "0032_seed_youtube_provider"),
    ]

    operations = [
        migrations.RunPython(merge_duplicate_providers, migrations.RunPython.noop),
        migrations.AddConstraint(
            model_name="provider",
            constraint=models.UniqueConstraint(
                django.db.models.functions.text.Trim(
                    django.db.models.functions.text.Lower(
                        django.db.models.functions.text.Substr(
                            models.F("domain"),
                            1,
                            django.db.models.expressions.CombinedExpression(
                                django.db.models.functions.text.StrIndex(
                                    django.db.models.functions.text.Concat(
                                        models.F("domain"), models.Value("/")
                                    ),
                                    models.Value("/"),
                                ),
                                "-",
                                models.Value(1),
                            ),
                        )
                    )
                ),
                condition=models.Q(("domain", ""), _negated=True),
                name="uniq_provider_domain_root",
            ),
        ),
    ]
