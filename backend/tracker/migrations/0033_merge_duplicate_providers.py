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


def _provider_ref(model, provider, provider_model) -> object:
    """The row of *provider* as seen by *model*'s registry.

    A historical model cannot be filtered by a live ``Provider`` instance
    ("Must be \"Provider\" instance"), so the provider is re-read through the
    same registry the model came from. Outside a migration (``provider_model``
    is ``None``) the objects share a registry already and the live instance is
    returned unchanged.
    """
    if provider_model is None:
        return provider
    return provider_model.objects.get(pk=provider.pk)


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


def _merge_provider(canonical, duplicate, apps=None) -> None:
    from availability.services.legacy_ingest import (
        delete_scraped_mapping,
        ingest_scraped_mapping,
    )
    from tracker.models import (
        ExternalProviderMapping,
        Region,
        ScrapedContentMapping,
    )

    # Every model queried below is resolved as a *historical* model when
    # ``apps`` is available (always, in a real migrate). A live-class queryset
    # SELECTs every column the live model declares, including columns added by
    # migrations that have not run yet at this point in the chain — so a live
    # query here makes the chain unextendable: adding a field to any of these
    # models breaks every fresh database with "column ... does not exist".
    #
    # This is not hypothetical. ``availability.0011`` added
    # ``SyncCursor.progress_at`` and the live ``SyncCursor.objects`` query at the
    # bottom of this function failed every fresh test database until these were
    # resolved historically. ``Subscription`` and ``ExternalProviderMapping``
    # were already fixed this way for the same reason (see below); the rest were
    # left live and are completed here.
    #
    # The live classes are imported only for the no-``apps`` fallback, which
    # exists because this function's own tests call it directly.
    if apps is not None:
        ContentMapping = apps.get_model("availability", "ContentMapping")
        Offer = apps.get_model("availability", "Offer")
        ProviderContent = apps.get_model("availability", "ProviderContent")
        SyncCursor = apps.get_model("availability", "SyncCursor")
        SyncRun = apps.get_model("availability", "SyncRun")
        Region = apps.get_model("tracker", "Region")
        ScrapedContentMapping = apps.get_model("tracker", "ScrapedContentMapping")
        ProviderRow = apps.get_model("tracker", "Provider")
    else:
        from availability.models import (
            ContentMapping,
            Offer,
            ProviderContent,
            SyncCursor,
            SyncRun,
        )
        from tracker.models import Region, ScrapedContentMapping
        ProviderRow = None

    # Resolved as a *historical* model when available: later migrations add
    # columns to ``Subscription`` (e.g. 0041's ``access_mode``), so a live-class
    # query here would SELECT a column that does not exist yet at this point in
    # the chain and every fresh database would fail. Filter by ``provider_id``
    # (a live ``Provider`` instance cannot be passed to a historical queryset).
    SubscriptionModel = (
        apps.get_model("tracker", "Subscription")
        if apps is not None else None
    )
    if SubscriptionModel is not None:
        for sub in SubscriptionModel.objects.filter(provider_id=duplicate.pk):
            if SubscriptionModel.objects.filter(
                user_id=sub.user_id, provider_id=canonical.pk
            ).exists():
                sub.delete()
            else:
                sub.provider_id = canonical.pk
                sub.save()
    else:
        from tracker.models import Subscription
        for sub in Subscription.objects.filter(provider=duplicate):
            if Subscription.objects.filter(user_id=sub.user_id, provider=canonical).exists():
                sub.delete()
            else:
                sub.provider = canonical
                sub.save()

    # Both sides are resolved as *historical* models when available. This
    # migration runs before tracker.0035 adds the country-aware ``country``
    # column, so a live-class query would SELECT a column that does not exist
    # yet and every fresh database would fail at this step. Passing a live
    # ``Provider`` instance into a historical query also raises
    # ("Must be \"Provider\" instance"), so the provider side is re-read as a
    # historical row too. Merge semantics are unchanged — at this point in
    # history every mapping row is unscoped, so (provider, integration_name)
    # is still the full key.
    MappingModel = (
        apps.get_model("tracker", "ExternalProviderMapping")
        if apps is not None else ExternalProviderMapping
    )
    ProviderModel = (
        apps.get_model("tracker", "Provider")
        if apps is not None else None
    )
    canonical_ref = (
        ProviderModel.objects.get(pk=canonical.pk) if ProviderModel else canonical
    )
    for mapping in list(MappingModel.objects.filter(provider_id=duplicate.pk)):
        if MappingModel.objects.filter(
            provider_id=canonical_ref.pk, integration_name=mapping.integration_name
        ).exists():
            mapping.delete()
        else:
            mapping.provider_id = canonical_ref.pk
            mapping.save()

    # Legacy scraper rows: fold into the canonical twin (same title/year) or,
    # with no twin, move the row over. Only rows that changed are re-ingested
    # at the end — ingestion is idempotent, but there is no point re-writing
    # the untouched majority.
    #
    # ``ScrapedContentMapping`` is a historical model here, so it cannot be
    # filtered by a live ``Provider`` instance; ``_scraped_*_ref`` resolves the
    # matching historical row (falling back to the live object outside a
    # migration, where the model is live too).
    scraped_duplicate = _provider_ref(ScrapedContentMapping, duplicate, ProviderRow)
    scraped_canonical = _provider_ref(ScrapedContentMapping, canonical, ProviderRow)
    reingest = []
    for row in ScrapedContentMapping.objects.filter(provider=scraped_duplicate).order_by("pk"):
        twin = (
            ScrapedContentMapping.objects.filter(
                provider=scraped_canonical,
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
            row.provider = scraped_canonical
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
    legacy_duplicate = _provider_ref(ContentMapping, duplicate, ProviderRow)
    ContentMapping.objects.filter(
        provider=legacy_duplicate, provider_content_id__startswith="legacy-"
    ).delete()
    ProviderContent.objects.filter(
        provider=_provider_ref(ProviderContent, duplicate, ProviderRow),
        provider_content_id__startswith="legacy-",
    ).delete()

    # ``_repoint`` filters and assigns through these historical models, so the
    # provider instances it receives must be historical rows too: passing the
    # live ``Provider`` raises 'Cannot query "X": Must be "Provider" instance'.
    canonical_row = _provider_ref(ProviderContent, canonical, ProviderRow)
    duplicate_row = _provider_ref(ProviderContent, duplicate, ProviderRow)

    _repoint(ProviderContent.objects.filter(provider=duplicate_row), canonical_row, True)
    _repoint(ContentMapping.objects.filter(provider=duplicate_row), canonical_row, False)
    _repoint(Offer.objects.filter(provider=duplicate_row), canonical_row, True)
    _repoint(SyncRun.objects.filter(provider=duplicate_row), canonical_row, False)
    _repoint(SyncCursor.objects.filter(provider=duplicate_row), canonical_row, False)

    for region in Region.objects.filter(provider=duplicate.slug):
        if Region.objects.filter(provider=canonical.slug, country=region.country).exists():
            region.delete()
        else:
            region.provider = canonical.slug
            region.save()

    if ProviderRow is not None:
        ProviderRow.objects.filter(pk=duplicate.pk).delete()
    else:
        duplicate.delete()

    for pk in reingest:
        ingest_scraped_mapping(ScrapedContentMapping.objects.get(pk=pk))


def merge_duplicate_providers(apps, schema_editor):
    # ``provider_domain_root`` is a pure string helper with no ORM access, so
    # importing it live is safe and is what keeps the grouping rule identical to
    # the one the model enforces. ``Provider`` itself must NOT be live: this
    # function queries it, and a live queryset SELECTs every column the live
    # model declares — including columns added by migrations later than this one.
    # That made the chain unextendable: adding a field to ``tracker.Provider``
    # broke every fresh database with
    #   ProgrammingError: column tracker_provider.<new> does not exist
    # raised right here. Verified by probe before and after the fix.
    #
    # ``apps`` is None only when this function is called directly by a test
    # (never by the migration framework), so the live model is used then — which
    # is correct, because a direct call runs against the *current* schema.
    from tracker.models import provider_domain_root

    if apps is not None:
        Provider = apps.get_model("tracker", "Provider")
    else:
        from tracker.models import Provider

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
            _merge_provider(canonical, duplicate, apps=apps)


class Migration(migrations.Migration):
    # The data merge deletes and updates rows that carry FK triggers; Postgres
    # refuses the index build for the constraint in the same transaction
    # ("pending trigger events"). RunPython still gets its own transaction —
    # only the two operations are decoupled, and the merge is idempotent if
    # the constraint step ever has to be retried on its own.
    atomic = False

    dependencies = [
        ("tracker", "0032_seed_youtube_provider"),
        # The merge repoints availability projections (Offer/ProviderContent/
        # ContentMapping) whose FKs point at tracker.Provider, and re-ingests
        # through the legacy bridge. Declaring the availability dependency is
        # what makes those tables exist first: without it Django is free to
        # apply this migration before availability.0001, and a brand-new
        # database fails here with 'relation "availability_contentmapping"
        # does not exist'. tracker.0003 in turn depends on availability.0001,
        # so this edge introduces no cycle.
        ("availability", "0003_providercontent_contentmapping_content_and_more"),
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
