"""Completion history for catalogue sources, derived from the run audit.

The problem this pins: a walk that deletes its ``SyncCursor`` row on completion
cannot record its own completion *in that row* — the delete destroys it. So
``last_successful_sync`` is a narrower thing than it sounds (last completed
traversal of a walk whose row still exists), and the source-level question
"when did this last finish?" is answered from ``RefreshRun``.

``RefreshRun`` already carried everything needed; what was missing was one
canonical reader. Three call sites had independently-written variants with
different filters, so "last successful run" could mean different things in
different places. ``last_successful_completions`` is now that reader, and
``stale_catalogue_sources`` uses it.
"""
from __future__ import annotations

import datetime as dt

from django.test import TestCase
from django.utils import timezone

from tracker.models import RefreshRun
from tracker.services.bulk_refresh import (
    last_successful_completion,
    last_successful_completions,
    stale_catalogue_sources,
)


def _run(service, *, age_minutes=0, finished=True, error="", abandoned=False):
    started = timezone.now() - dt.timedelta(minutes=age_minutes)
    return RefreshRun.objects.create(
        service=service,
        started_at=started,
        finished_at=(started + dt.timedelta(minutes=1)) if finished else None,
        error=error,
        abandoned_at=(started if abandoned else None),
    )


class CompletionDerivationTests(TestCase):
    def test_a_successful_run_is_a_completion(self):
        run = _run("tubi", age_minutes=60)
        self.assertEqual(last_successful_completion("tubi"), run.finished_at)

    def test_a_failed_run_is_not_a_completion(self):
        _run("tubi", age_minutes=60, error="boom")
        self.assertIsNone(last_successful_completion("tubi"))

    def test_an_unfinished_run_is_not_a_completion(self):
        _run("tubi", age_minutes=60, finished=False)
        self.assertIsNone(last_successful_completion("tubi"))

    def test_an_abandoned_run_is_not_a_completion(self):
        """A reconciled run records a dead worker, not a finished walk.

        This is the case the older ``stale_catalogue_sources`` query got right
        only by convention: it filtered ``error=""`` and the cleanup command
        happens to write a non-empty error alongside ``abandoned_at``. Relying
        on that coincidence is fragile, so the filter is now explicit.
        """
        _run("tubi", age_minutes=60, abandoned=True)
        self.assertIsNone(last_successful_completion("tubi"))

    def test_the_newest_successful_run_wins(self):
        _run("tubi", age_minutes=600)
        newest = _run("tubi", age_minutes=60)
        self.assertEqual(last_successful_completion("tubi"), newest.finished_at)

    def test_a_later_failure_does_not_erase_an_earlier_completion(self):
        """Completion is history, not "the current state is healthy"."""
        good = _run("tubi", age_minutes=600)
        _run("tubi", age_minutes=60, error="boom")
        self.assertEqual(last_successful_completion("tubi"), good.finished_at)

    def test_a_source_with_no_runs_has_no_completion(self):
        self.assertIsNone(last_successful_completion("never_run"))

    def test_batch_form_is_one_query_and_covers_every_slug(self):
        _run("tubi", age_minutes=60)
        _run("pbs", age_minutes=120)
        _run("rai_play", age_minutes=30, error="boom")
        with self.assertNumQueries(1):
            result = last_successful_completions(["tubi", "pbs", "rai_play"])
        self.assertEqual(set(result), {"tubi", "pbs"})

    def test_empty_input_issues_no_query(self):
        with self.assertNumQueries(0):
            self.assertEqual(last_successful_completions([]), {})

    def test_completion_does_not_depend_on_a_synccursor_row(self):
        """The whole point: no checkpoint row is involved in the answer."""
        from availability.models import SyncCursor

        _run("tubi", age_minutes=60)
        self.assertEqual(SyncCursor.objects.count(), 0)
        self.assertIsNotNone(last_successful_completion("tubi"))


class StaleCatalogueSourcesTests(TestCase):
    """The consumer keeps its behaviour, with the filter now explicit."""

    def test_an_abandoned_run_does_not_make_a_source_look_fresh(self):
        from tracker.services.bulk_refresh import _catalogue_source_slugs

        slugs, _disabled = _catalogue_source_slugs()
        self.assertTrue(slugs, "no catalogue sources registered")
        slug = slugs[0]
        RefreshRun.objects.filter(service=slug).delete()
        _run(slug, age_minutes=5, abandoned=True)
        # With only an abandoned run, the source must still read stale.
        self.assertIn(slug, stale_catalogue_sources())

    def test_a_recent_successful_run_makes_a_source_fresh(self):
        from tracker.services.bulk_refresh import _catalogue_source_slugs

        slugs, _disabled = _catalogue_source_slugs()
        slug = slugs[0]
        RefreshRun.objects.filter(service=slug).delete()
        _run(slug, age_minutes=5)
        self.assertNotIn(slug, stale_catalogue_sources())
