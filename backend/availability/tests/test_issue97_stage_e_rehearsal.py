"""Test-only Stage E policy rehearsal on the existing PostgreSQL ledger.

Only synthetic targets and fixture responses are accepted here. This fixture
has no real transport, signing code, or application entry point.
"""

from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from unittest.mock import Mock, patch
from urllib.parse import urlsplit
from uuid import uuid4

from django.db import connection, transaction
from django.test import SimpleTestCase, TransactionTestCase

from availability.models import AvailabilityExperiment, AvailabilityExperimentScope
from availability.services.experiment_runner import (
    APPROVED_MANIFEST,
    APPROVED_SCOPES,
    ExperimentBlocked,
    _completed_scope_ids,
    _incomplete_scope_ids,
    _record_outcome,
    database_identity,
    validate_manifest,
    validate_scope,
)
from config.test_database import require_disposable_database
from tracker.services.refresh_requests import title_drain_lock


START = datetime(2030, 1, 1, tzinfo=timezone.utc)
FIRST_PARTY_GAP = timedelta(seconds=3)
MAX_UNCERTAINTY = timedelta(seconds=5)


@dataclass(frozen=True)
class Scope:
    title: str
    source: str
    method: str
    endpoint: str

    @property
    def key(self):
        return f"{self.title}-{self.source}"

    @property
    def slot_start(self):
        return START if self.title == "title-1" else START + timedelta(minutes=30)


SCOPES = (
    Scope("title-1", "first-party-1", "GET", "https://fp1.invalid/title-1"),
    Scope("title-1", "first-party-2", "GET", "https://fp2.invalid/title-1"),
    Scope("title-1", "index", "POST", "https://index.invalid/graphql"),
    Scope("title-2", "first-party-3", "GET", "https://fp3.invalid/title-2"),
    Scope("title-2", "index", "POST", "https://index.invalid/graphql"),
)


@dataclass(frozen=True)
class Clock:
    utc: datetime
    monotonic_seconds: int
    uncertainty: timedelta | None = MAX_UNCERTAINTY

    def check(self, scope):
        if self.uncertainty is None or not timedelta() <= self.uncertainty <= MAX_UNCERTAINTY:
            raise ExperimentBlocked("clock uncertainty")
        if not scope.slot_start <= self.utc - self.uncertainty:
            raise ExperimentBlocked("outside title slot")
        if not self.utc + self.uncertainty < scope.slot_start + timedelta(minutes=30):
            raise ExperimentBlocked("outside title slot")


@dataclass(frozen=True)
class Response:
    status: int
    end: Clock
    shape_valid: bool = True


class BatchFixture:
    def __init__(self, experiment, *, expires_at=START + timedelta(hours=1)):
        self.experiment = experiment
        self.expires_at = expires_at
        self.transport = Mock()

    def capture(self, scope, start):
        if scope not in SCOPES:
            raise ExperimentBlocked("outside fixture allowlist")

        with title_drain_lock() as acquired:
            if not acquired:
                raise ExperimentBlocked("another capture holds the fixture lock")
            completed = _completed_scope_ids(self.experiment.pk)
            if _incomplete_scope_ids(self.experiment.pk, completed):
                raise ExperimentBlocked("incomplete scope requires reconciliation")
            if scope.key in completed:
                return "already completed"
            if scope != next(item for item in SCOPES if item.key not in completed):
                raise ExperimentBlocked("previous scope or title checkpoint required")
            start.check(scope)
            if start.utc + start.uncertainty >= self.expires_at:
                raise ExperimentBlocked("fixture expiry reached")

            previous = self.experiment.attempts.filter(outcome__isnull=False)
            if scope.method == "GET":
                previous_get = previous.filter(method="GET").order_by("-ordinal").first()
                if previous_get and start.utc - previous_get.outcome.recorded_at < FIRST_PARTY_GAP:
                    raise ExperimentBlocked("first-party pacing")

            with patch("django.utils.timezone.now", return_value=start.utc), transaction.atomic(durable=True):
                attempt = self.experiment.attempts.create(
                    ordinal=len(completed) + 1, scope_id=scope.key, source_slug=scope.source,
                    operation="synthetic-capture", method=scope.method,
                    destination_host=urlsplit(scope.endpoint).hostname,
                )

            try:
                response = self.transport(scope)
            except (TimeoutError, InterruptedError) as exc:
                raise ExperimentBlocked("transmission unknown; reservation spent") from exc

            ok = response.status == 200
            with patch("django.utils.timezone.now", return_value=response.end.utc):
                _record_outcome(attempt, "success" if ok else "failure", response.status)
            if not ok:
                raise ExperimentBlocked(f"HTTP {response.status}; reservation spent")
            if not response.shape_valid:
                raise ExperimentBlocked("unexpected response shape")
            response.end.check(scope)
            if response.end.utc < start.utc or response.end.monotonic_seconds < start.monotonic_seconds:
                raise ExperimentBlocked("unordered clock")

            # Fixture clocks all carry a five-second bound or less. Use the
            # conservative five-second bound for every durable earlier capture.
            title_attempts = previous.filter(scope_id__startswith=f"{scope.title}-")
            earliest = title_attempts.earliest("reserved_at").reserved_at
            if response.end.utc - earliest + 2 * MAX_UNCERTAINTY > timedelta(minutes=10):
                raise ExperimentBlocked("combined observation span exceeds ten minutes")

            AvailabilityExperimentScope.objects.create(experiment=self.experiment, scope_id=scope.key)
            return "completed"


class StageEBatchARuntimeBoundaryTests(SimpleTestCase):
    def test_fixed_manifest_refuses_batch_caps_and_first_party_scope(self):
        with self.assertRaisesRegex(ExperimentBlocked, "fixed 6-attempt ceiling"):
            validate_manifest(dict(APPROVED_MANIFEST, maximum_attempts=5))
        with self.assertRaisesRegex(ExperimentBlocked, "differs from the fixed approved scope set"):
            validate_manifest(dict(APPROVED_MANIFEST, scopes=[
                dict(scope, maximum_attempts=1) for scope in APPROVED_SCOPES
            ]))
        with self.assertRaisesRegex(ExperimentBlocked, "not an exact member of the approved manifest"):
            validate_scope(dict(APPROVED_SCOPES[0], source_slug="fixture-first-party"))


class StageEBatchARehearsalTests(TransactionTestCase):
    def setUp(self):
        require_disposable_database(connection)
        self.new_fixture()

    def new_fixture(self):
        self.experiment = AvailabilityExperiment.objects.create(
            authorization_id=uuid4(), manifest_digest="fixture", database_identity=database_identity(),
            maximum_attempts=len(SCOPES),
        )
        self.audit = BatchFixture(self.experiment)

    def sample(self, scope=SCOPES[0], *, seconds=10, uncertainty=5):
        return Clock(scope.slot_start + timedelta(seconds=seconds), seconds,
                     None if uncertainty is None else timedelta(seconds=uncertainty))

    def prepare(self, scope=SCOPES[0], *, start=10, end=11, status=200, valid=True):
        self.audit.transport.return_value = Response(status, self.sample(scope, seconds=end), valid)
        return self.sample(scope, seconds=start)

    def restart(self):
        connection.close()
        self.audit = BatchFixture(AvailabilityExperiment.objects.get(pk=self.experiment.pk))

    def counts(self, spent, confirmed, unknown, checkpoints):
        attempts = self.experiment.attempts
        self.assertEqual(attempts.count(), spent)
        self.assertEqual(attempts.filter(outcome__isnull=False).count(), confirmed)
        self.assertEqual(attempts.filter(outcome__isnull=True).count(), unknown)
        self.assertEqual(self.experiment.scopes.count(), checkpoints)

    def title_checkpoints(self):
        completed = _completed_scope_ids(self.experiment.pk)
        return {title for title in ("title-1", "title-2")
                if all(scope.key in completed for scope in SCOPES if scope.title == title)}

    def test_five_scopes_and_two_title_checkpoints_survive_restart(self):
        for index, scope in enumerate(SCOPES):
            start = self.prepare(scope, start=10 + index * 4, end=11 + index * 4)
            self.assertEqual(self.audit.capture(scope, start), "completed")
            if index == 2:
                self.counts(3, 3, 0, 3)
                self.restart()
                self.assertEqual(self.title_checkpoints(), {"title-1"})
        self.counts(5, 5, 0, 5)
        self.assertEqual(self.title_checkpoints(), {"title-1", "title-2"})
        self.assertEqual(list(self.experiment.attempts.order_by("ordinal").values_list("ordinal", flat=True)),
                         [1, 2, 3, 4, 5])
        original_times = list(self.experiment.attempts.order_by("ordinal").values_list(
            "reserved_at", "outcome__recorded_at"))
        self.restart()
        for scope in SCOPES:
            self.assertEqual(self.audit.capture(scope, self.sample(scope, seconds=1801)), "already completed")
        self.audit.transport.assert_not_called()
        self.counts(5, 5, 0, 5)
        self.assertEqual(list(self.experiment.attempts.order_by("ordinal").values_list(
            "reserved_at", "outcome__recorded_at")), original_times)

    def test_wrong_target_title_order_and_bad_clocks_stop_before_reservation(self):
        with self.assertRaisesRegex(ExperimentBlocked, "fixture allowlist"):
            self.audit.capture(Scope("title-1", "first-party-1", "GET", "https://wrong.invalid/"), self.sample())
        with self.assertRaisesRegex(ExperimentBlocked, "checkpoint required"):
            self.audit.capture(SCOPES[3], self.sample(SCOPES[3]))
        for sample in (self.sample(uncertainty=None), self.sample(uncertainty=6),
                       self.sample(seconds=4), self.sample(seconds=1795)):
            with self.subTest(clock=sample), self.assertRaisesRegex(ExperimentBlocked, "clock|slot"):
                self.audit.capture(SCOPES[0], sample)
        self.audit.transport.assert_not_called()
        self.counts(0, 0, 0, 0)

    def test_expiry_uncertainty_stops_before_reservation(self):
        self.audit = BatchFixture(self.experiment, expires_at=START + timedelta(seconds=15))
        with self.assertRaisesRegex(ExperimentBlocked, "fixture expiry reached"):
            self.audit.capture(SCOPES[0], self.sample(seconds=10))
        self.audit.transport.assert_not_called()
        self.counts(0, 0, 0, 0)

    def test_pacing_is_read_from_postgres_on_restart(self):
        self.audit.capture(SCOPES[0], self.prepare())
        self.restart()
        with self.assertRaisesRegex(ExperimentBlocked, "first-party pacing"):
            self.audit.capture(SCOPES[1], self.sample(seconds=13))
        self.audit.transport.assert_not_called()
        self.counts(1, 1, 0, 1)
        self.audit.capture(SCOPES[1], self.prepare(SCOPES[1], start=14, end=15))
        self.counts(2, 2, 0, 2)

    def test_independent_connection_sees_reservation_before_timeout(self):
        def timeout(scope):
            other = connection.copy()
            try:
                with other.cursor() as cursor:
                    cursor.execute("SELECT count(*) FROM availability_availabilityexperimentattempt "
                                   "WHERE experiment_id = %s AND scope_id = %s", [self.experiment.pk, scope.key])
                    self.assertEqual(cursor.fetchone(), (1,))
            finally:
                other.close()
            raise TimeoutError

        self.audit.transport.side_effect = timeout
        with self.assertRaisesRegex(ExperimentBlocked, "transmission unknown"):
            self.audit.capture(SCOPES[0], self.sample())
        self.audit.transport.assert_called_once_with(SCOPES[0])
        self.restart()
        for scope in SCOPES[:2]:
            with self.assertRaisesRegex(ExperimentBlocked, "requires reconciliation"):
                self.audit.capture(scope, self.sample())
        self.audit.transport.assert_not_called()
        self.counts(1, 0, 1, 0)

    def test_received_stop_responses_and_invalid_shape_are_confirmed_and_never_retried(self):
        for status, valid in ((302, True), (403, True), (429, True), (503, True), (200, False)):
            with self.subTest(status=status, shape_valid=valid):
                self.new_fixture()
                with self.assertRaisesRegex(ExperimentBlocked, "HTTP|shape"):
                    self.audit.capture(SCOPES[0], self.prepare(status=status, valid=valid))
                self.assertEqual(self.experiment.attempts.get().outcome.http_status, status)
                self.audit.transport.assert_called_once_with(SCOPES[0])
                self.restart()
                with self.assertRaisesRegex(ExperimentBlocked, "requires reconciliation"):
                    self.audit.capture(SCOPES[1], self.sample())
                self.audit.transport.assert_not_called()
                self.counts(1, 1, 0, 0)

    def test_out_of_slot_unordered_and_over_span_responses_cannot_checkpoint(self):
        for end in (1795, 9, 601):
            with self.subTest(end=end):
                self.new_fixture()
                with self.assertRaisesRegex(ExperimentBlocked, "slot|unordered|span"):
                    self.audit.capture(SCOPES[0], self.prepare(end=end))
                self.counts(1, 1, 0, 0)

    def test_wall_time_in_order_does_not_mask_monotonic_clock_regression(self):
        start = self.prepare()
        response = self.audit.transport.return_value
        self.audit.transport.return_value = replace(response, end=replace(response.end, monotonic_seconds=9))
        with self.assertRaisesRegex(ExperimentBlocked, "unordered clock"):
            self.audit.capture(SCOPES[0], start)
        self.counts(1, 1, 0, 0)

    def test_boundary_span_is_inclusive_but_requires_all_title_captures(self):
        self.audit.capture(SCOPES[0], self.prepare())
        self.audit.capture(SCOPES[1], self.prepare(SCOPES[1], start=14, end=600))
        with self.assertRaisesRegex(ExperimentBlocked, "combined observation span"):
            self.audit.capture(SCOPES[2], self.prepare(SCOPES[2], start=600, end=601))
        self.counts(3, 3, 0, 2)
        self.assertEqual(self.title_checkpoints(), set())

    def test_interruption_after_response_before_checkpoint_requires_reconciliation(self):
        with patch.object(AvailabilityExperimentScope.objects, "create", side_effect=InterruptedError):
            with self.assertRaises(InterruptedError):
                self.audit.capture(SCOPES[0], self.prepare())
        self.restart()
        with self.assertRaisesRegex(ExperimentBlocked, "requires reconciliation"):
            self.audit.capture(SCOPES[0], self.sample())
        self.audit.transport.assert_not_called()
        self.counts(1, 1, 0, 0)
