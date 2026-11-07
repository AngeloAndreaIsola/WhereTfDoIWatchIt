"""Source roles, and the scheduling decision they exist to answer.

The one question this module answers
------------------------------------
    "Which sources are allowed to run for this title?"

Everything else here follows from taking that question seriously. The
question it used to be answered with was *"which sources have a
title-targeted fetch?"* — and that substitution is the architectural bug this
module replaces, because it silently conflates three different things:

1. **what a source is** (the provider itself, a reseller of many providers,
   a metered reseller), which is a *policy* fact someone decided;
2. **what a source can be asked** (per-title vs whole-catalogue), which is a
   *capability* fact the code determines;
3. **whether a source should run now** (has a cheaper source already
   answered?), which is a *resolution* fact that changes minute to minute.

Deriving policy from (2) produces three specific failures, all of which this
repo had:

- a metered aggregator looked like a first-party source, because its
  capability flags say "title-level" and nothing said otherwise — so the
  scheduler ran it unconditionally while the policy described it as a
  fallback;
- a catalogue collector was either invisible to scheduling or (had it been
  included) would have been asked a question it must raise ``ValueError`` to
  answer, because "can be asked per title" was mistaken for "is worth
  running";
- a demotion policy could be written, reported, and displayed without
  changing a single enqueue — the report described a ladder the scheduler
  did not climb.

So roles are **declared, not derived**. :data:`SOURCE_ROLES` is the single
list, it is exhaustive over the registry, and a test fails when a registered
source is missing from it. That exhaustiveness check is what stops the
next new source from silently inheriting the default role.

Roles
-----
``COLLECTOR``
    A first-party source. Its own answer about its own service is
    authoritative, and it is cheap: no metered budget, no reseller. Two
    shapes, and the distinction is preserved because it is load-bearing:

    - *title-addressable* (Mediaset Infinity, YouTube) — can be asked about
      one film directly;
    - *catalogue-driven* (Prime Video, BBC iPlayer, CBC Gem, Tubi, Pluto TV,
      RaiPlay, PBS, ABC iview) — **cannot** be asked about one film. Its
      ``fetch(content)`` raises ``ValueError`` by design. It contributes by
      having synced its catalogue, not by answering a per-title question.

``BROAD_AGGREGATOR``
    JustWatch, Watchmode. Resells many providers' catalogues, so it widens
    coverage far beyond any single first-party source. While JustWatch is
    unmetered, Watchmode operates under a finite monthly plan quota (2,500
    calls/mo; local budget 2,200) managed by the quota ledger.
    Explicitly **not** ordered against each other: both are consulted, and
    neither waits for the other.

``METERED_AGGREGATOR``
    Streaming Availability. Same shape as a broad aggregator, but every call
    consumes a scarce daily budget (100 req/day), so it is consulted **only
    for titles no cheaper source has resolved**.

The ladder, and the reason it is not a chain
---------------------------------------------
The naive shape is a strict chain — collectors, then aggregators, then the
metered one. That is wrong for a reason worth stating: it makes the metered
source wait for *every* cheaper source to finish, so one slow collector
delays the only source that can answer, and a broken collector stalls the
pipeline indefinitely.

The shape that is correct is a fan-in through a resolver::

    title ──┬── collectors ────────────┐
            ├── JustWatch ─────────────┤
            ├── Watchmode ─────────────┼──▶ resolver ──▶ is more work needed?
            └── Streaming Availability ┘

Every source may run; the *resolver* decides whether the metered one earned
its call. That is what makes "a collector already found Prime Video" a
sufficient reason to skip a metered request, and it is checked **before the
request is created** — otherwise the run has already paid scheduling,
storage and worker time for a request the policy would have rejected.
"""
from __future__ import annotations

import enum
import logging
import os

logger = logging.getLogger(__name__)


class AvailabilityRole(str, enum.Enum):
    """What a source *is*, for scheduling purposes.

    A ``str`` enum on purpose: these values are persisted in
    ``AvailabilityRefresh.source_policy`` and cross the API boundary, and a
    plain string keeps a row written by an older build readable by a newer
    one.
    """

    COLLECTOR = "collector"
    BROAD_AGGREGATOR = "broad_aggregator"
    METERED_AGGREGATOR = "metered_aggregator"

    def __str__(self) -> str:  # pragma: no cover - display convenience
        return self.value


class PolicySkipReason(str, enum.Enum):
    """Why a source was not enqueued for a title.

    A **closed vocabulary** on purpose. A skip is a scheduling decision, and
    an auditable one has to be queryable: a free-text reason would drift into
    a dozen near-synonyms and stop answering "why is this source idle?".

    It holds exactly one member, and that is deliberate rather than
    incomplete. ``not_applicable`` is a genuine member of
    :class:`tracker.models.AvailabilityResolutionReason` — it means a source
    cannot serve this title's scope — and duplicating the *string* here would
    put one value in both vocabularies. That is precisely the ambiguity this
    enum exists to prevent: a UI keying off the string could render a
    scheduling skip as an availability verdict. A test asserts the two sets
    are disjoint, and it caught exactly this when the duplicate was written.

    None of these is an availability verdict. ``NOT_NEEDED`` means *we chose
    not to ask*, which is a statement about the scheduler, not about the
    world. Merging the two is the bug the reason split in
    :mod:`tracker.services.availability_report` exists to prevent.
    """

    NOT_NEEDED = "not_needed"

    def __str__(self) -> str:  # pragma: no cover - display convenience
        return self.value

    @classmethod
    def all(cls) -> tuple[str, ...]:
        return tuple(r.value for r in cls)


#: The authoritative role of every registered availability source.
#:
#: **Exhaustive by construction and checked by a test.** A registered source
#: absent from this mapping is a hard error, not a default: silently
#: defaulting a new provider to "collector" is how the next metered source
#: ends up running unconditionally.
#:
#: Note what is *not* in here: nothing is inferred from ``capabilities`` or
#: from ``Source.type``. Both are legitimate facts about a source, and both
#: are wrong answers to "what should the scheduler do with it".
SOURCE_ROLES: dict[str, AvailabilityRole] = {
    # --- first-party, title-addressable -----------------------------------
    "mediaset_infinity": AvailabilityRole.COLLECTOR,
    "youtube": AvailabilityRole.COLLECTOR,
    # --- first-party, catalogue-driven (cannot answer per title) -----------
    "prime_video": AvailabilityRole.COLLECTOR,
    "bbc_iplayer": AvailabilityRole.COLLECTOR,
    "cbc_gem": AvailabilityRole.COLLECTOR,
    "tubi": AvailabilityRole.COLLECTOR,
    "pluto_tv": AvailabilityRole.COLLECTOR,
    "rai_play": AvailabilityRole.COLLECTOR,
    "pbs": AvailabilityRole.COLLECTOR,
    "abc_iview": AvailabilityRole.COLLECTOR,
    # --- broad aggregators: multi-provider coverage (Watchmode has finite quota) -
    "justwatch": AvailabilityRole.BROAD_AGGREGATOR,
    "watchmode": AvailabilityRole.BROAD_AGGREGATOR,
    # --- metered aggregator: scarce daily budget, consulted last ----------
    "streaming_availability": AvailabilityRole.METERED_AGGREGATOR,
}

#: Environment variable holding the ladder switch. Named here so the variable
#: name lives next to the policy that reads it.
LADDER_ENV_VAR = "AVAILABILITY_CONSULT_METERED_ONLY_WHEN_UNRESOLVED"


def consult_metered_only_when_unresolved() -> bool:
    """Whether a metered aggregator may run only for unresolved titles.

    This is the switch the whole ladder exists to express, and it is **off by
    default** so that switching the policy on is a measured, reversible
    decision rather than a side effect of deploying this module.

    **Read on every call, deliberately, rather than captured at import.** The
    earlier module-level constant had two problems, both of which this
    function exists to remove:

    1. *Deployment.* An import-time read means changing the environment does
       nothing until the process restarts, so an operator who flips the
       variable sees no change and reasonably concludes the setting is
       broken. Reading on demand makes the environment genuinely live.
    2. *Tests.* A module constant is process state that the test runner
       inherits from whatever environment launched it. Ten tests broke the
       moment the flag was enabled in the dev containers — not because any of
       them tested the ladder, but because they asserted on drain counts that
       the gate changed. That is the test suite being coupled to deployment
       configuration, which is exactly the coupling this removes: a test that
       cares about the setting now sets it explicitly.

    The report still always computes the counterfactual, so the decision can
    be made from a number (:func:`counterfactual`) rather than an intuition.
    """
    return os.environ.get(LADDER_ENV_VAR, "0") == "1"


def role_of(slug: str) -> AvailabilityRole | None:
    return SOURCE_ROLES.get(slug)


def is_policy_metered(slug: str) -> bool:
    """Whether the **architecture** treats this source as scarce.

    **Policy, not configuration.** This is the flag the scheduler gates on: it
    reads :data:`SOURCE_ROLES`, a deliberate statement about how much a source's
    answer is worth and how expensive it is to obtain.

    It is intentionally *not* the same question as
    :func:`tracker.services.availability_report.has_configured_rate_limit`
    ("does a technical quota exist?"). Gating on the configuration question
    instead would tie scheduling to whichever rate limits happen to be set
    today, so adding a quota to a free source — or removing one from a scarce
    one — would silently change the hierarchy with no policy change at all.
    Naming them differently is what makes that substitution hard to do by
    accident.
    """
    return SOURCE_ROLES.get(slug) is AvailabilityRole.METERED_AGGREGATOR


def missing_roles(registered_slugs) -> list[str]:
    """Registered sources with no declared role — must always be empty.

    Returned rather than raised so a test can assert the invariant without
    the report itself hard-failing on a newly added source.
    """
    return [slug for slug in registered_slugs if slug not in SOURCE_ROLES]


#: Display groups, for a report that reads as tiers without inventing a
#: fourth dimension.
ROLE_TITLES: dict[str, str] = {
    AvailabilityRole.COLLECTOR.value: "Collectors",
    AvailabilityRole.BROAD_AGGREGATOR.value: "Broad aggregators",
    AvailabilityRole.METERED_AGGREGATOR.value: "Metered aggregator",
}


def sources_by_role(registered_slugs=None) -> dict[str, list[str]]:
    """``{role_value: [slug, ...]}`` for the registered sources.

    ``registered_slugs`` defaults to the live registry so the caller cannot
    report a source that does not exist; passing an explicit list is for
    tests and for reporting a deliberately restricted policy scope.
    """
    if registered_slugs is None:
        from availability.sources import source_registry

        registered_slugs = source_registry.slugs()
    out: dict[str, list[str]] = {role.value: [] for role in AvailabilityRole}
    for slug in registered_slugs:
        role = SOURCE_ROLES.get(slug)
        if role is not None:
            out[role.value].append(slug)
    return out


def allowed_sources_for_title(
    title_state: str,
    *,
    applicable_slugs=None,
    ladder_switch: bool | None = None,
) -> dict[str, list[str]]:
    """Which sources may run for a title in this state, grouped by role.

    ``title_state`` is ``"available"``, ``"unavailable"``, ``"unknown"``, or
    ``"resolved"`` (a resolved title is available *or* unavailable — both
    mean some source answered, which is the only thing the metered-source
    question cares about).

    The decision, stated once:

    - a title that **any** applicable source has resolved needs no further
      source, so a metered aggregator is excluded;
    - a title that **no** source has resolved (still unknown) may consult the
      metered aggregator, because it is the only source left that can answer;
    - a title that is unresolved *only because a cheaper source has not run
      yet* is the interesting case: the metered source is still excluded
      while that cheaper request is outstanding, which is what stops the
      budget being spent on a title the pending check was about to answer.

    Non-metered sources are never gated by this — they are free, and running
    them early is what produces the evidence the metered source would
    otherwise be paying for.
    """
    if ladder_switch is None:
        ladder_switch = consult_metered_only_when_unresolved()
    grouped = sources_by_role(applicable_slugs)
    if applicable_slugs is not None:
        keep = set(applicable_slugs)
        grouped = {role: [s for s in slugs if s in keep] for role, slugs in grouped.items()}

    if not ladder_switch:
        # Policy off: every applicable source may run. The roles are still
        # reported, so the display and the counterfactual work identically.
        return grouped

    metered: list[str] = []
    if title_state in ("unknown",):
        metered = list(grouped.get(AvailabilityRole.METERED_AGGREGATOR.value, []))
    grouped[AvailabilityRole.METERED_AGGREGATOR.value] = metered
    return grouped


def decide_sources_for_title(
    report_entry: dict,
    *,
    applicable_slugs=None,
    ladder_switch: bool | None = None,
) -> dict:
    """The scheduling decision for one title, as an auditable object.

    Returns::

        {
          "allowed_sources": ["mediaset_infinity", "justwatch", "watchmode"],
          "skipped_sources": {"streaming_availability": "not_needed"},
          "reasons":         {"streaming_availability": "not_needed"},
          "gate": True,
          "counterfactual": {...},
        }

    **Why a decision and not a list.** A bare list of allowed sources cannot
    answer "why is Streaming Availability idle?", and that is the question the
    whole hierarchy exists to raise. The three states the UI needs to
    distinguish are all in here, and they are genuinely different:

    ==========================  ==================================
    ``skipped_sources[...]``    what to render
    ==========================  ==================================
    ``not_needed``              "not requested — title already resolved"
    ``not_applicable``          "not applicable to this title"
    (absent)                    "queued"
    (present, but blocked)      "blocked — quota exhausted"
    ==========================  ==================================

    ``reasons`` duplicates ``skipped_sources`` by name only to keep the
    payload self-describing for consumers that store one and render the
    other; both always hold the same value, and a test pins that.

    **Resolution-aware, not sequential.** The inputs are the *current
    evidence* from ``title_source_report`` — which sources produced an active
    offer, which have definitively answered, which have not spoken. So:

    - a collector or JustWatch offer resolves the title, and the metered
      source is skipped as ``not_needed``;
    - two aggregators answering "no" does **not** resolve anything, so the
      metered source stays eligible;
    - a *withdrawn* offer is not an answer, so it does not gate.

    A strict A-then-B-then-C ladder would get the middle case wrong by
    declaring the title resolved once the aggregators had spoken.
    """
    if ladder_switch is None:
        ladder_switch = consult_metered_only_when_unresolved()

    if applicable_slugs is None:
        applicable_slugs = sorted(
            s for s in SOURCE_ROLES if s in _registered_slugs()
        )

    state = (report_entry or {}).get("state", "unknown")
    produced = {
        row["source"]
        for row in (report_entry or {}).get("sources", [])
        if row.get("available")
    }

    metered_slugs = {
        slug for slug, role in SOURCE_ROLES.items()
        if role is AvailabilityRole.METERED_AGGREGATOR
    }
    free_slugs = [s for s in applicable_slugs if s not in metered_slugs]
    metered = [s for s in applicable_slugs if s in metered_slugs]

    skipped: dict[str, str] = {}
    allowed_free = list(free_slugs)

    if not ladder_switch:
        allowed_metered = list(metered)
    elif state == "unknown":
        # Nothing has answered. The metered source is the only one left that
        # can, so it stays eligible.
        allowed_metered = list(metered)
    else:
        # Available or unavailable — either way something answered, so the
        # title is resolved and the scarce source has nothing left to add.
        allowed_metered = []
        for slug in metered:
            skipped[slug] = PolicySkipReason.NOT_NEEDED.value

    return {
        "allowed_sources": allowed_free + allowed_metered,
        "skipped_sources": skipped,
        "reasons": dict(skipped),
        "gate": ladder_switch,
        "state": state,
        "resolved_by": sorted(produced),
        "counterfactual": {
            "gate": ladder_switch,
            "metered_would_run_for": sorted(
                slug for slug in metered
                if not ladder_switch or state == "unknown"
            ),
        },
    }


def _registered_slugs():
    from availability.sources import source_registry

    return set(source_registry.slugs())


def policy_description() -> dict:
    """The active policy, so it is never implicit in a report."""
    return {
        "roles": {slug: role.value for slug, role in sorted(SOURCE_ROLES.items())},
        "groups": sources_by_role(),
        "consult_metered_only_when_unresolved": consult_metered_only_when_unresolved(),
        "ladder": (
            "collectors + broad aggregators run freely; the metered aggregator "
            "runs only for titles no other source has resolved"
            if consult_metered_only_when_unresolved()
            else "every applicable title-level source may run (metered-source "
            "gating is OFF; the counterfactual reports what switching it on "
            "would cost)"
        ),
    }


def counterfactual(title_states: dict[int, str], resolved_sources: dict[int, set[str]]) -> dict:
    """What switching the metered gate ON would cost, without spending anything.

    ``title_states`` is the current ``{movie_id: "available"|"unavailable"|
    "unknown"}`` classification, and ``resolved_sources`` the matching
    ``{movie_id: {source slugs that produced an offer}}`` — both from
    :func:`tracker.services.availability_report.title_source_report`, passed
    in rather than re-derived so the projection costs no extra queries.

    The projection is deliberately crude and is stated as such: a currently
    available title whose *only* available offer came from a metered source
    would become **unknown** (not "unavailable") if the metered source
    stopped answering, because an unanswered question is not evidence of
    absence.

    Read-only: it enqueues nothing, contacts no provider, and spends no
    quota. Its purpose is to make the decision measurable before it is made.
    """
    metered = {
        slug for slug, role in SOURCE_ROLES.items()
        if role is AvailabilityRole.METERED_AGGREGATOR
    }

    still_resolved = 0
    losing = 0
    for movie_id, state in title_states.items():
        if state != "available":
            # Already unresolved: gating cannot make it worse.
            continue
        produced = resolved_sources.get(movie_id, set())
        if produced & metered and not (produced - metered):
            losing += 1
        else:
            still_resolved += 1

    return {
        "policy": "consult_metered_only_when_unresolved",
        "current": consult_metered_only_when_unresolved(),
        "titles_still_resolved": still_resolved,
        "titles_losing_their_answer": losing,
        "metered_sources": sorted(metered),
        "note": (
            "Counterfactual only — no source was consulted and no quota was "
            "spent. A title whose only available offer came from a metered "
            "source would fall back to 'unknown', not to 'unavailable': an "
            "unanswered question is not evidence of absence."
        ),
    }
