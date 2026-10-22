"""Repair the collapsed Watchmode provider-content identities (F4).

Background
----------
``WatchmodeSource`` used to key ``ProviderContent`` on ``wm:{source_id}``,
where ``source_id`` is Watchmode's **provider** id (203 = Netflix, 296 =
Tubi). Every title that provider carried therefore shared one identity:

* one ``ProviderContent`` row, titled after whichever title was refreshed
  first, claiming to be a catalogue entry;
* one ``ContentMapping`` for the *first* title, which made reconciliation
  report ``mapping points to tmdb N`` for every other title;
* every other offer was hidden (``currently_available=False`` in the
  reconciled view) even though the observation itself was valid.

The source now writes ``wm:{provider}:{content_type}:tmdb-{id}`` (see
:mod:`.watchmode_identity`). This module repairs the historical rows:
it splits each collapsed row into one row per observed title, re-keys the
offers, moves or creates the per-title mapping, re-keys ``OfferEvidence`` and
``OfferPrice``, and retires the collapsed ``ProviderContent`` row.

Safety model
------------
* **Dry run is the default.** ``--apply`` additionally requires the SHA-256
  digest printed by a reviewed dry run; a plan that changed since review is
  refused.
* **Fail closed.** A collapsed row whose mapping names a title it has no
  offers for, an offer identity that collides with an existing row, or an
  unexpected extra reference blocks that row (reported, not silently
  guessed). ``--apply`` refuses to run if any row is blocked.
* **Scoped by construction.** Only ``ProviderContent.provider_content_id``
  values matching the exact pre-fix format ``wm:<provider>`` (no further
  colon) are touched; offers are updated only if they belong to such a row.
  No other provider, source or mapping is read for writing.
* **One transaction.** Every write happens inside a single
  ``transaction.atomic()`` after ``SELECT ... FOR UPDATE`` on the affected
  rows, and the plan is rebuilt inside it and compared with the reviewed
  digest, so concurrent drift aborts the run.
* **Idempotent.** After a successful apply no collapsed row remains, so a
  second run plans zero changes.
* **Retirement is explicit.** A collapsed row is deleted only after every
  offer and mapping that referenced it has been moved off it, and only when
  the row is not referenced by anything else. Its data is fully preserved on
  the split rows (or in the database backup); the collapsed row itself is
  confirmed invalid by construction (a provider id is not a content id).
* **Writer coordination is the operator's.** Watchmode is a title-targeted
  source; the refresh service is its only writer. ``--apply`` refuses to run
  while a Watchmode ``SyncRun`` is ``running`` and requires the explicit
  ``--assume-workers-paused`` acknowledgement, because a live refresh that
  starts mid-apply could re-create the collapsed identity. (The advisory-lock
  barrier lives in the separate remediation change set and is deliberately
  not imported here.)
"""
from __future__ import annotations

import hashlib
import json
import logging
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Iterable, Optional

from django.db import transaction
from django.utils import timezone

from ..domain.enums import MatchMethod, SyncStatus
from ..models import (
    ContentMapping,
    Offer,
    OfferEvidence,
    OfferPrice,
    ProviderContent,
    Source,
    SyncRun,
    build_offer_identity,
    build_provider_content_identity,
)
from .watchmode_identity import (
    build_watchmode_content_id,
    collapsed_provider_key,
    is_collapsed_watchmode_content_id,
)

logger = logging.getLogger(__name__)

#: Confidence for a mapping derived from the title the offer was observed for.
#: Same value the source's own external-id path records; the association is
#: the exact TMDb id the refresh requested, not a fuzzy title match.
DERIVED_MAPPING_CONFIDENCE = 0.95


class RepairError(Exception):
    """The repair cannot proceed safely."""


@dataclass(frozen=True)
class OfferMove:
    offer_id: int
    old_identity: str
    new_identity: str
    country_code: str
    provider_slug: str
    content_type: str
    tmdb_id: int
    old_content_id: str
    new_content_id: str


@dataclass(frozen=True)
class NewContent:
    provider_id: int
    provider_slug: str
    old_content_id: str
    new_content_id: str
    content_type: str
    tmdb_id: int
    season_number: Optional[int]
    episode_number: Optional[int]
    title: str
    release_date: Optional[date]
    provider_url: str
    first_seen: datetime
    last_seen: datetime
    currently_available: bool
    offer_ids: tuple[int, ...]
    #: Existing mapping to move onto this identity (``None`` to create one).
    move_mapping_id: Optional[int] = None
    move_mapping_confidence: Optional[float] = None
    move_mapping_method: str = ""


@dataclass(frozen=True)
class EvidenceMove:
    evidence_id: int
    old_identity: str
    new_identity: str
    #: Existing evidence row at ``new_identity`` for the same source. The plan
    #: records it so apply can prove the merge decision was reviewed.
    merge_into_id: Optional[int] = None


@dataclass(frozen=True)
class PriceMove:
    price_id: int
    old_identity: str
    new_identity: str
    #: Existing price row at ``new_identity`` for the same source.
    merge_into_id: Optional[int] = None


@dataclass(frozen=True)
class CollapsedRow:
    content_id: int
    provider_slug: str
    old_content_id: str


@dataclass(frozen=True)
class RetainedRow:
    """A collapsed row that a filtered plan deliberately leaves in place.

    A verification-filtered repair may move only externally corroborated
    offers.  Anything not selected -- an unresolved offer, its mapping, or an
    entire title group containing an unresolved offer -- stays on the original
    collapsed row.  The row therefore cannot be retired, and the plan records
    exactly what remains so the omission is reviewable rather than silent.
    """

    content_id: int
    provider_slug: str
    old_content_id: str
    offer_ids: tuple[int, ...]
    mapping_ids: tuple[int, ...]
    reasons: tuple[str, ...]


@dataclass
class RepairPlan:
    contents: tuple[NewContent, ...] = ()
    offers: tuple[OfferMove, ...] = ()
    evidence: tuple[EvidenceMove, ...] = ()
    prices: tuple[PriceMove, ...] = ()
    retire: tuple[CollapsedRow, ...] = ()
    blocked: tuple[dict, ...] = ()
    retained: tuple[RetainedRow, ...] = ()
    #: ``None`` means the ordinary all-offers repair.  A tuple is an explicit
    #: verification allowlist; only those offers may move, and everything else
    #: remains on its collapsed row.
    selected_offer_ids: Optional[tuple[int, ...]] = None
    generated_at: datetime = field(default_factory=timezone.now)

    # ------------------------------------------------------------------ views
    @property
    def is_empty(self) -> bool:
        return not (self.contents or self.offers or self.evidence
                    or self.prices or self.retire or self.retained)

    def counts(self) -> dict:
        counts = {
            "collapsed_rows": len({row.content_id for row in self.retire}),
            "content_rows_to_create_or_update": len(self.contents),
            "offers_to_move": len(self.offers),
            "mappings_to_move": sum(1 for c in self.contents if c.move_mapping_id),
            "mappings_to_create": sum(1 for c in self.contents if not c.move_mapping_id),
            "evidence_rows_to_move": len(self.evidence),
            "price_rows_to_move": len(self.prices),
            "blocked_rows": len(self.blocked),
        }
        if self.selected_offer_ids is not None:
            counts["offers_retained"] = sum(
                len(row.offer_ids) for row in self.retained
            )
            counts["rows_retained"] = len(self.retained)
        return counts

    def digest_payload(self) -> dict:
        """Canonical, timestamp-free projection used for the review digest.

        Covers every value apply writes: the derived content rows (including
        title/date/URL and the observation window), the offer identity moves,
        the evidence/price rekeys *and their planned merge target*, the rows to
        retire, and any blocked rows. Collections that are semantically
        unordered are sorted so an equivalent plan hashes identically.
        """
        payload = {
            "contents": sorted(
                (
                    {
                        "provider": c.provider_slug,
                        "old": c.old_content_id,
                        "new": c.new_content_id,
                        "content_type": c.content_type,
                        "tmdb_id": c.tmdb_id,
                        "season": c.season_number,
                        "episode": c.episode_number,
                        "title": c.title,
                        "release_date": c.release_date,
                        "provider_url": c.provider_url,
                        "first_seen": c.first_seen,
                        "last_seen": c.last_seen,
                        "currently_available": c.currently_available,
                        "offers": sorted(c.offer_ids),
                        "move_mapping_id": c.move_mapping_id,
                        "move_mapping_confidence": c.move_mapping_confidence,
                        "move_mapping_method": c.move_mapping_method,
                    }
                    for c in self.contents
                ),
                key=lambda row: (row["provider"], row["new"]),
            ),
            "offers": sorted(
                (
                    {
                        "offer_id": o.offer_id,
                        "old_identity": o.old_identity,
                        "new_identity": o.new_identity,
                    }
                    for o in self.offers
                ),
                key=lambda row: row["offer_id"],
            ),
            "evidence": sorted(
                (
                    {
                        "evidence_id": e.evidence_id,
                        "old_identity": e.old_identity,
                        "new_identity": e.new_identity,
                        "merge_into_id": e.merge_into_id,
                    }
                    for e in self.evidence
                ),
                key=lambda row: row["evidence_id"],
            ),
            "prices": sorted(
                (
                    {
                        "price_id": p.price_id,
                        "old_identity": p.old_identity,
                        "new_identity": p.new_identity,
                        "merge_into_id": p.merge_into_id,
                    }
                    for p in self.prices
                ),
                key=lambda row: row["price_id"],
            ),
            "retire": sorted(row.content_id for row in self.retire),
            "blocked": sorted(
                self.blocked,
                key=lambda row: (
                    row.get("content_row") or 0,
                    str(row.get("content_id") or ""),
                ),
            ),
        }
        if self.selected_offer_ids is not None:
            # Keep the ordinary all-offers digest byte-for-byte unchanged.
            # A filtered plan commits to both the allowlist and the rows it
            # deliberately leaves behind.
            payload["selection"] = sorted(self.selected_offer_ids)
            payload["retained"] = sorted(
                (
                    {
                        "content_row": row.content_id,
                        "provider": row.provider_slug,
                        "content_id": row.old_content_id,
                        "offers": sorted(row.offer_ids),
                        "mappings": sorted(row.mapping_ids),
                        "reasons": list(row.reasons),
                    }
                    for row in self.retained
                ),
                key=lambda row: row["content_row"],
            )
        return payload

    def digest(self) -> str:
        payload = json.dumps(
            self.digest_payload(), sort_keys=True, separators=(",", ":"),
            default=str,
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def as_dict(self) -> dict:
        data = self.digest_payload()
        data["counts"] = self.counts()
        data["digest"] = self.digest()
        data["generated_at"] = self.generated_at.isoformat()
        return data


# --------------------------------------------------------------------- plan


def build_plan(
    include_offer_ids: Optional[Iterable[int]] = None,
) -> RepairPlan:
    """Read-only plan for collapsed Watchmode content identities.

    ``include_offer_ids=None`` is the ordinary all-offers repair.  Passing an
    explicit iterable enables a verification-filtered plan: only those offers
    may move.  A title group containing any unselected offer is retained in
    full, and a collapsed row is retired only when nothing remains on it.
    """
    selected_offer_ids = (
        None
        if include_offer_ids is None
        else tuple(sorted({int(value) for value in include_offer_ids}))
    )
    selected_set = (
        None if selected_offer_ids is None else set(selected_offer_ids)
    )

    collapsed_rows = [
        row
        for row in ProviderContent.objects.select_related("provider").filter(
            provider_content_id__startswith="wm:"
        ).order_by("id")
        if is_collapsed_watchmode_content_id(row.provider_content_id)
    ]
    if not collapsed_rows:
        blocked = []
        if selected_set:
            blocked.append({
                "reasons": [
                    "selection includes offers not on a collapsed row: "
                    + ", ".join(str(value) for value in sorted(selected_set))
                ],
            })
        return RepairPlan(
            blocked=tuple(blocked),
            selected_offer_ids=selected_offer_ids,
        )

    contents: list[NewContent] = []
    offer_moves: list[OfferMove] = []
    evidence_moves: list[EvidenceMove] = []
    price_moves: list[PriceMove] = []
    retire: list[CollapsedRow] = []
    retained: list[RetainedRow] = []
    blocked: list[dict] = []
    selected_seen: set[int] = set()

    for row in collapsed_rows:
        provider_key = collapsed_provider_key(row.provider_content_id)
        offers = list(
            Offer.objects.filter(
                provider_id=row.provider_id,
                provider_content_id=row.provider_content_id,
            ).select_related("country").order_by("id")
        )
        mappings = list(
            ContentMapping.objects.filter(
                provider_id=row.provider_id,
                provider_content_id=row.provider_content_id,
            ).order_by("id")
        )
        if selected_set is not None:
            selected_seen.update({offer.id for offer in offers} & selected_set)

        row_blocked = _blocking_reasons(row, offers, mappings)
        if row_blocked:
            blocked.append({
                "content_row": row.id,
                "provider": row.provider.slug,
                "content_id": row.provider_content_id,
                "reasons": row_blocked,
            })
            continue

        groups: dict[tuple, list[Offer]] = defaultdict(list)
        for offer in offers:
            groups[(
                offer.content_type,
                offer.tmdb_id,
                offer.season_number,
                offer.episode_number,
            )].append(offer)

        if selected_set is not None:
            # A group is atomic.  If even one offer in it is unverified, keep
            # the whole title on the collapsed row rather than split a title
            # between a reviewed identity and an undispositioned one.
            groups = {
                key: group
                for key, group in groups.items()
                if all(offer.id in selected_set for offer in group)
            }
            if not groups:
                retained.append(RetainedRow(
                    content_id=row.id,
                    provider_slug=row.provider.slug,
                    old_content_id=row.provider_content_id,
                    offer_ids=tuple(offer.id for offer in offers),
                    mapping_ids=tuple(mapping.id for mapping in mappings),
                    reasons=(
                        "no title group is fully selected for the filtered plan",
                    ),
                ))
                continue

        mapping_by_target = {}
        for mapping in mappings:
            mapping_by_target[(mapping.content_type, mapping.tmdb_id, None, None)] = mapping

        if selected_set is None:
            # A collapsed mapping with no observed offer is still evidence of
            # which title the identity was created for; split it from the
            # mapping alone.  A filtered plan must not invent a verified title
            # from a mapping whose offers were all excluded.
            for key, mapping in list(mapping_by_target.items()):
                if key not in groups:
                    groups[key] = []

        movies = _movies_for({key[1] for key in groups})

        new_contents: list[NewContent] = []
        row_moves: list[OfferMove] = []
        row_evidence: list[EvidenceMove] = []
        row_prices: list[PriceMove] = []
        aborted = False
        for key, group_offers in sorted(groups.items(), key=_group_sort_key):
            content_type, tmdb_id, season, episode = key
            new_id = build_watchmode_content_id(
                provider_key, content_type, tmdb_id=tmdb_id
            )
            if len(new_id) > 100:  # ProviderContent.provider_content_id limit
                blocked.append({
                    "content_row": row.id,
                    "content_id": row.provider_content_id,
                    "reasons": [f"derived identity too long: {new_id!r}"],
                })
                aborted = True
                break

            movie = movies.get((tmdb_id, content_type))
            mapping = mapping_by_target.pop(key, None)
            new_contents.append(NewContent(
                provider_id=row.provider_id,
                provider_slug=row.provider.slug,
                old_content_id=row.provider_content_id,
                new_content_id=new_id,
                content_type=content_type,
                tmdb_id=tmdb_id,
                season_number=season,
                episode_number=episode,
                title=(movie.title if movie else ""),
                release_date=(
                    date(movie.year, 1, 1) if movie and movie.year else None
                ),
                provider_url=next(
                    (o.provider_url for o in group_offers if o.provider_url), ""
                ),
                first_seen=(
                    min(o.first_seen for o in group_offers) if group_offers
                    else row.first_seen
                ),
                last_seen=(
                    max(o.last_seen for o in group_offers) if group_offers
                    else row.last_seen
                ),
                currently_available=(
                    any(o.currently_available for o in group_offers)
                    if group_offers else row.currently_available
                ),
                offer_ids=tuple(o.id for o in group_offers),
                move_mapping_id=(mapping.id if mapping else None),
                move_mapping_confidence=(mapping.confidence if mapping else None),
                move_mapping_method=(mapping.match_method if mapping else ""),
            ))
            for offer in group_offers:
                new_identity = build_offer_identity(
                    row.provider.slug,
                    offer.country.code,
                    offer.content_type,
                    offer.tmdb_id,
                    offer.season_number,
                    offer.episode_number,
                    offer.offer_type,
                    new_id,
                )
                row_moves.append(OfferMove(
                    offer_id=offer.id,
                    old_identity=offer.identity,
                    new_identity=new_identity,
                    country_code=offer.country.code,
                    provider_slug=row.provider.slug,
                    content_type=offer.content_type,
                    tmdb_id=offer.tmdb_id,
                    old_content_id=row.provider_content_id,
                    new_content_id=new_id,
                ))
                row_evidence.extend(_evidence_moves(offer.identity, new_identity))
                row_prices.extend(_price_moves(offer.identity, new_identity))

        if aborted or not new_contents:
            continue

        leftover = sorted(mapping_by_target.values(), key=lambda m: m.id)
        if selected_set is None and leftover:
            blocked.append({
                "content_row": row.id,
                "content_id": row.provider_content_id,
                "reasons": [
                    "mapping(s) name a title with no observed offer: "
                    + ", ".join(str(m.id) for m in leftover)
                ],
            })
            continue

        collision = _identity_collisions(row_moves)
        if collision:
            blocked.append({
                "content_row": row.id,
                "content_id": row.provider_content_id,
                "reasons": collision,
            })
            continue

        contents.extend(new_contents)
        offer_moves.extend(row_moves)
        evidence_moves.extend(row_evidence)
        price_moves.extend(row_prices)

        if selected_set is None:
            retire.append(CollapsedRow(
                content_id=row.id,
                provider_slug=row.provider.slug,
                old_content_id=row.provider_content_id,
            ))
            continue

        moved_ids = {move.offer_id for move in row_moves}
        retained_offers = [offer for offer in offers if offer.id not in moved_ids]
        if retained_offers or leftover:
            reasons = []
            if retained_offers:
                reasons.append(
                    "offers not selected or in a fully selected title group: "
                    + ", ".join(str(offer.id) for offer in retained_offers)
                )
            if leftover:
                reasons.append(
                    "mapping(s) retained because their title has no selected "
                    "offer: " + ", ".join(str(mapping.id) for mapping in leftover)
                )
            retained.append(RetainedRow(
                content_id=row.id,
                provider_slug=row.provider.slug,
                old_content_id=row.provider_content_id,
                offer_ids=tuple(offer.id for offer in retained_offers),
                mapping_ids=tuple(mapping.id for mapping in leftover),
                reasons=tuple(reasons),
            ))
        else:
            retire.append(CollapsedRow(
                content_id=row.id,
                provider_slug=row.provider.slug,
                old_content_id=row.provider_content_id,
            ))

    if selected_set is not None:
        # A second filtered dry run must be clean.  Selected offers that are
        # already on a split Watchmode row are completed work, not unknown
        # input.  Missing offers, native identities and still-collapsed rows
        # remain hard errors so a typo cannot silently shrink the repair.
        completed = selected_set - selected_seen
        unexpected: list[int] = []
        if completed:
            content_ids = dict(
                Offer.objects.filter(pk__in=completed).values_list(
                    "id", "provider_content_id"
                )
            )
            for offer_id in sorted(completed):
                content_id = content_ids.get(offer_id)
                if (
                    content_id is None
                    or not content_id.startswith("wm:")
                    or is_collapsed_watchmode_content_id(content_id)
                ):
                    unexpected.append(offer_id)
        if unexpected:
            blocked.append({
                "reasons": [
                    "selection includes offers not on a collapsed or "
                    "already-split Watchmode row: "
                    + ", ".join(str(value) for value in sorted(unexpected))
                ],
            })

    # Duplicate collapse rows for the same provider id (e.g. a manual retry
    # created a second row) would target the same new identity. Refuse rather
    # than guess which one wins.
    seen: dict[str, int] = {}
    conflicts = []
    for content in contents:
        key = content.new_content_id
        if key in seen:
            conflicts.append(key)
        seen[key] = content.provider_id
    if conflicts:
        blocked.append({
            "reasons": ["duplicate target identity: " + ", ".join(sorted(set(conflicts)))],
        })

    return RepairPlan(
        contents=tuple(contents),
        offers=tuple(offer_moves),
        evidence=tuple(evidence_moves),
        prices=tuple(price_moves),
        retire=tuple(retire),
        blocked=tuple(blocked),
        retained=tuple(retained),
        selected_offer_ids=selected_offer_ids,
    )


def _group_sort_key(item) -> tuple:
    key = item[0]
    return tuple("" if value is None else str(value) for value in key)


def _blocking_reasons(row, offers, mappings) -> list[str]:
    reasons: list[str] = []
    if not offers and not mappings:
        reasons.append(
            "collapsed row has neither offers nor a mapping; the title it "
            "represents cannot be recovered without inventing one"
        )
    if len(mappings) > 1:
        reasons.append(f"multiple mappings on one collapsed identity: {len(mappings)}")
    if not row.provider_id:
        reasons.append("provider missing")
    for mapping in mappings:
        if mapping.content is not None and mapping.content_id != row.id:
            reasons.append(
                f"mapping {mapping.id} already links a different content row"
            )
    return reasons


def _identity_collisions(moves: Iterable[OfferMove]) -> list[str]:
    problems: list[str] = []
    for move in moves:
        clash = Offer.objects.filter(identity=move.new_identity).exclude(
            pk=move.offer_id
        ).exists()
        if clash:
            problems.append(
                f"offer {move.offer_id} target identity already exists: "
                f"{move.new_identity}"
            )
    return problems


def _evidence_moves(old_identity: str, new_identity: str) -> list[EvidenceMove]:
    moves = []
    for evidence in OfferEvidence.objects.filter(
        offer_identity=old_identity
    ).order_by("id"):
        clash = (
            OfferEvidence.objects.filter(
                offer_identity=new_identity, source_id=evidence.source_id
            )
            .exclude(pk=evidence.pk)
            .order_by("id")
            .first()
        )
        moves.append(
            EvidenceMove(
                evidence_id=evidence.id,
                old_identity=old_identity,
                new_identity=new_identity,
                merge_into_id=(clash.id if clash else None),
            )
        )
    return moves


def _price_moves(old_identity: str, new_identity: str) -> list[PriceMove]:
    moves = []
    for price in OfferPrice.objects.filter(
        offer_identity=old_identity
    ).order_by("id"):
        clash = (
            OfferPrice.objects.filter(
                offer_identity=new_identity, source_id=price.source_id
            )
            .exclude(pk=price.pk)
            .order_by("id")
            .first()
        )
        moves.append(
            PriceMove(
                price_id=price.id,
                old_identity=old_identity,
                new_identity=new_identity,
                merge_into_id=(clash.id if clash else None),
            )
        )
    return moves


def _movies_for(tmdb_ids) -> dict:
    if not tmdb_ids:
        return {}
    from tracker.models import Movie  # local: availability must not depend on tracker at import time

    return {
        (movie.tmdb_id, movie.content_type): movie
        for movie in Movie.objects.filter(tmdb_id__in=set(tmdb_ids))
    }


# -------------------------------------------------------------------- apply


def apply_plan(plan: RepairPlan, *, expected_digest: str,
               workers_paused: bool = False) -> dict:
    """Apply a reviewed plan in one transaction.

    ``expected_digest`` is mandatory: the operator must pass the digest of the
    dry run they reviewed. The plan is rebuilt inside the transaction; any
    drift aborts before a single write.
    """
    if not workers_paused:
        raise RepairError(
            "refusing to apply: pass --assume-workers-paused after stopping the "
            "watchmode refresh workers"
        )
    if not expected_digest:
        raise RepairError("refusing to apply without the reviewed plan digest")

    running = SyncRun.objects.filter(
        source__slug="watchmode", status=SyncStatus.RUNNING
    )
    if running.exists():
        raise RepairError(
            "refusing to apply: a watchmode SyncRun is still running "
            f"({running.count()}); stop the workers first"
        )

    with transaction.atomic():
        locked = list(
            ProviderContent.objects.select_for_update().filter(
                id__in=[row.content_id for row in plan.retire]
            )
        )
        if len(locked) != len({row.content_id for row in plan.retire}):
            raise RepairError("a planned content row disappeared before apply")

        current = build_plan(include_offer_ids=plan.selected_offer_ids)
        if current.blocked:
            raise RepairError(
                "refusing to apply: plan now has blocked rows: "
                + json.dumps(current.blocked, default=str)
            )
        if current.digest() != expected_digest:
            raise RepairError(
                "refusing to apply: the database changed since the reviewed dry "
                f"run (reviewed {expected_digest}, now {current.digest()}); "
                "run a new dry run"
            )

        _lock_offers(current)
        watchmode = Source.objects.filter(slug="watchmode").first()
        if watchmode is None:
            raise RepairError("the watchmode Source row is missing")

        rows_by_new_id: dict[str, ProviderContent] = {}
        for content in current.contents:
            rows_by_new_id[content.new_content_id] = _upsert_content(content, watchmode)

        for content in current.contents:
            _upsert_mapping(content, rows_by_new_id[content.new_content_id], watchmode)

        for move in current.offers:
            _move_offer(move, rows_by_new_id[move.new_content_id])

        for evidence in current.evidence:
            _rekey_evidence(evidence)

        for price in current.prices:
            _rekey_price(price)

        for row in current.retire:
            _retire(row)

        _verify(current)

    logger.info(
        "watchmode_identity_repair_applied digest=%s counts=%s",
        current.digest(), json.dumps(current.counts(), sort_keys=True),
    )
    return {
        "applied": True,
        "digest": current.digest(),
        "counts": current.counts(),
    }


def _lock_offers(plan: RepairPlan):
    ids = [move.offer_id for move in plan.offers]
    if ids:
        locked = set(
            Offer.objects.select_for_update().filter(id__in=ids)
            .values_list("id", flat=True)
        )
        if locked != set(ids):
            raise RepairError("an offer row disappeared before apply")


def _upsert_content(content: NewContent, watchmode: Source) -> ProviderContent:
    existing = ProviderContent.objects.filter(
        identity=_content_identity(content)
    ).first()
    if existing is not None:
        # A post-fix refresh may already have created the split row. Merge
        # observation windows instead of creating a duplicate.
        changed = []
        if content.first_seen and content.first_seen < existing.first_seen:
            existing.first_seen = content.first_seen
            changed.append("first_seen")
        if content.last_seen and content.last_seen > existing.last_seen:
            existing.last_seen = content.last_seen
            changed.append("last_seen")
        if content.currently_available and not existing.currently_available:
            existing.currently_available = True
            changed.append("currently_available")
        if not existing.title and content.title:
            existing.title = content.title
            changed.append("title")
        if not existing.provider_url and content.provider_url:
            existing.provider_url = content.provider_url
            changed.append("provider_url")
        if changed:
            existing.save(update_fields=[*changed, "updated_at"])
        return existing

    return ProviderContent.objects.create(
        provider_id=content.provider_id,
        source=watchmode,
        provider_content_id=content.new_content_id,
        content_type=content.content_type,
        title=content.title,
        release_date=content.release_date,
        season_number=content.season_number,
        episode_number=content.episode_number,
        provider_url=content.provider_url,
        metadata={"external_ids": {"tmdb": str(content.tmdb_id)}},
        first_seen=content.first_seen,
        last_seen=content.last_seen,
        currently_available=content.currently_available,
    )


def _content_identity(content: NewContent) -> str:
    from ..models import build_provider_content_identity

    return build_provider_content_identity(
        content.provider_slug,
        content.new_content_id,
        content.content_type,
        content.season_number,
        content.episode_number,
    )


def _upsert_mapping(content: NewContent, row: ProviderContent, watchmode: Source):
    target = ContentMapping.objects.filter(
        provider_id=content.provider_id,
        provider_content_id=content.new_content_id,
    ).first()

    if content.move_mapping_id:
        moved = ContentMapping.objects.filter(pk=content.move_mapping_id).first()
        if moved is None:
            raise RepairError(f"mapping {content.move_mapping_id} disappeared")
        if target is not None and target.id != moved.id:
            raise RepairError(
                f"mapping target {content.new_content_id} already exists ({target.id})"
            )
        moved.provider_content_id = content.new_content_id
        moved.content = row
        moved.last_verified = content.last_seen
        moved.save(update_fields=[
            "provider_content_id", "content", "last_verified", "updated_at",
        ])
        return

    if target is not None:
        if target.tmdb_id != content.tmdb_id:
            raise RepairError(
                f"existing mapping {target.id} points at tmdb {target.tmdb_id}, "
                f"not {content.tmdb_id}"
            )
        return

    ContentMapping.objects.create(
        provider_id=content.provider_id,
        source=watchmode,
        provider_content_id=content.new_content_id,
        content_type=content.content_type,
        tmdb_id=content.tmdb_id,
        content=row,
        confidence=DERIVED_MAPPING_CONFIDENCE,
        match_method=MatchMethod.EXTERNAL_ID,
        last_verified=content.last_seen,
    )


def _move_offer(move: OfferMove, row: ProviderContent):
    offer = Offer.objects.select_for_update().get(pk=move.offer_id)
    if offer.provider_content_id != move.old_content_id:
        raise RepairError(
            f"offer {move.offer_id} no longer belongs to {move.old_content_id}"
        )
    offer.provider_content_id = move.new_content_id
    offer.identity = move.new_identity
    offer.save(update_fields=[
        "provider_content_id", "identity", "updated_at",
    ])


def _rekey_evidence(move: EvidenceMove):
    evidence = OfferEvidence.objects.select_for_update().get(pk=move.evidence_id)
    clash = OfferEvidence.objects.filter(
        offer_identity=move.new_identity, source_id=evidence.source_id
    ).exclude(pk=evidence.pk).first()
    live_merge = clash.id if clash is not None else None
    if live_merge != move.merge_into_id:
        raise RepairError(
            f"evidence {move.evidence_id}: the reviewed plan expected merge "
            f"target {move.merge_into_id}, the database now has {live_merge}; "
            "run a new dry run"
        )
    if clash is not None:
        clash.is_present = clash.is_present or evidence.is_present
        clash.authoritative = clash.authoritative or evidence.authoritative
        clash.first_seen = min(clash.first_seen, evidence.first_seen)
        clash.last_seen = max(clash.last_seen, evidence.last_seen)
        clash.save(update_fields=[
            "is_present", "authoritative", "first_seen", "last_seen", "updated_at",
        ])
        evidence.delete()
        return
    evidence.offer_identity = move.new_identity
    evidence.save(update_fields=["offer_identity", "updated_at"])


def _rekey_price(move: PriceMove):
    price = OfferPrice.objects.select_for_update().get(pk=move.price_id)
    clash = OfferPrice.objects.filter(
        offer_identity=move.new_identity, source_id=price.source_id
    ).exclude(pk=price.pk).first()
    live_merge = clash.id if clash is not None else None
    if live_merge != move.merge_into_id:
        raise RepairError(
            f"price {move.price_id}: the reviewed plan expected merge target "
            f"{move.merge_into_id}, the database now has {live_merge}; "
            "run a new dry run"
        )
    if clash is not None:
        if price.observed_at and (
            clash.observed_at is None or price.observed_at > clash.observed_at
        ):
            clash.amount = price.amount
            clash.currency = price.currency
            clash.observed_at = price.observed_at
            clash.is_current = price.is_current
            clash.validation_error = price.validation_error
            clash.save(update_fields=[
                "amount", "currency", "observed_at", "is_current",
                "validation_error", "updated_at",
            ])
        price.delete()
        return
    price.offer_identity = move.new_identity
    price.save(update_fields=["offer_identity", "updated_at"])


def _retire(row: CollapsedRow):
    content = ProviderContent.objects.filter(pk=row.content_id).first()
    if content is None:
        return
    remaining_offers = Offer.objects.filter(
        provider_id=content.provider_id,
        provider_content_id=row.old_content_id,
    ).count()
    remaining_mappings = ContentMapping.objects.filter(
        provider_id=content.provider_id,
        provider_content_id=row.old_content_id,
    ).count()
    if remaining_offers or remaining_mappings:
        raise RepairError(
            f"refusing to retire {row.old_content_id}: "
            f"{remaining_offers} offers / {remaining_mappings} mappings still point at it"
        )
    content.delete()


def _verify(plan: RepairPlan):
    leftovers = [
        row.old_content_id
        for row in plan.retire
        if ProviderContent.objects.filter(pk=row.content_id).exists()
    ]
    if leftovers:
        raise RepairError(f"retired rows still present: {leftovers}")

    moved = Offer.objects.filter(pk__in=[m.offer_id for m in plan.offers])
    for move in plan.offers:
        if moved.filter(pk=move.offer_id, identity=move.new_identity).count() != 1:
            raise RepairError(f"offer {move.offer_id} was not moved exactly once")

    if plan.contents:
        missing = [
            c.new_content_id for c in plan.contents
            if not ProviderContent.objects.filter(
                identity=_content_identity(c)
            ).exists()
        ]
        if missing:
            raise RepairError(f"split content rows missing after apply: {missing}")

    for row in plan.retained:
        if not ProviderContent.objects.filter(pk=row.content_id).exists():
            raise RepairError(
                f"retained collapsed row {row.old_content_id} disappeared during apply"
            )
        for offer_id in row.offer_ids:
            offer = Offer.objects.filter(pk=offer_id).first()
            if offer is None or offer.provider_content_id != row.old_content_id:
                raise RepairError(
                    f"retained offer {offer_id} is no longer on "
                    f"{row.old_content_id}"
                )
        for mapping_id in row.mapping_ids:
            mapping = ContentMapping.objects.filter(pk=mapping_id).first()
            if mapping is None or mapping.provider_content_id != row.old_content_id:
                raise RepairError(
                    f"retained mapping {mapping_id} is no longer on "
                    f"{row.old_content_id}"
                )
