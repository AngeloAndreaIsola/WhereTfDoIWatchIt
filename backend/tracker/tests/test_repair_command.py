"""Tests for the guarded canonical-title repair command (#112 remediation tooling).

Dry-run default, exact-target confirmation, independent TMDb identity check,
override refusal, idempotency, and audit recording. Offline: TMDb mocked.
"""

import json
from io import StringIO
from unittest.mock import patch

import requests
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import IntegrityError, connections
from django.test import TestCase, TransactionTestCase
from django.test.utils import CaptureQueriesContext

from tracker.models import Movie, MovieIdentityAudit, RefreshRequest, RefreshRequestStatus
from tracker.services.tmdb import TMDbItem

CMD = "tracker.management.commands.repair_canonical_title.TMDbClient"


def _target_token(alias="default"):
    target = connections[alias].settings_dict
    return f"{target.get('NAME') or ''}@{target.get('HOST') or ''}"


def _tmdb_details(**overrides):
    return TMDbItem(**{
        "tmdb_id": 164,
        "title": "Breakfast at Tiffany's",
        "year": 1961,
        "content_type": "movie",
        **overrides,
    })


class RepairCanonicalTitleTests(TestCase):
    def setUp(self):
        self.movie = Movie.objects.create(
            tmdb_id=164, title="Natural Born Killers", year=1961, content_type="movie",
            overview="Holly Golightly is an eccentric New York City playgirl...",
        )

    def _run(self, *extra, error=None):
        out = StringIO()
        if error:
            with self.assertRaisesMessage(CommandError, error):
                call_command("repair_canonical_title", "--movie-id", str(self.movie.pk), *extra, stdout=out)
            return out.getvalue()
        call_command("repair_canonical_title", "--movie-id", str(self.movie.pk), *extra, stdout=out)
        return out.getvalue()

    def test_dry_run_is_default_and_writes_nothing(self):
        with patch(CMD) as mock_cls:
            payload = json.loads(self._run())
        mock_cls.assert_not_called()  # no live metadata call in dry run
        self.assertEqual(payload["status"], "dry_run")
        self.assertEqual(payload["before"]["title"], "Natural Born Killers")
        self.movie.refresh_from_db()
        self.assertEqual(self.movie.title, "Natural Born Killers")
        self.assertEqual(MovieIdentityAudit.objects.count(), 0)

    def test_apply_requires_exact_confirmation(self):
        with patch(CMD) as mock_cls, self.assertNumQueries(0):
            self._run("--apply", "--confirm-target", "wrong@target", error="exact target token")
        mock_cls.assert_not_called()
        self.movie.refresh_from_db()
        self.assertEqual(self.movie.title, "Natural Born Killers")

    def test_apply_repairs_title_and_audits(self):
        genuine = Movie.objects.create(tmdb_id=241, title="Natural Born Killers", year=1994)
        series = Movie.objects.create(tmdb_id=164, content_type="series", title="Same-ID series")
        history = RefreshRequest.objects.create(movie=self.movie, status=RefreshRequestStatus.DONE)
        old_movie = Movie.objects.values().get(pk=self.movie.pk)
        other_titles = Movie.objects.filter(pk__in=(genuine.pk, series.pk)).order_by("pk").values()
        old_other_titles = list(other_titles)
        old_history = RefreshRequest.objects.values().get(pk=history.pk)
        with patch(CMD) as mock_cls:
            mock_cls.return_value.details.return_value = _tmdb_details()
            payload = json.loads(self._run("--apply", "--confirm-target", _target_token()))
        mock_cls.return_value.details.assert_called_once_with(164, content_type="movie")
        self.assertEqual(payload["status"], "repaired")
        self.assertEqual(payload["before"]["references"]["refresh_requests"], 1)
        self.assertEqual(
            Movie.objects.values().get(pk=self.movie.pk),
            {**old_movie, "title": "Breakfast at Tiffany's", "canonical_title": "Breakfast at Tiffany's"},
        )
        self.assertEqual(list(other_titles.all()), old_other_titles)
        self.assertEqual(RefreshRequest.objects.values().get(pk=history.pk), old_history)
        audit = MovieIdentityAudit.objects.get(movie=self.movie)
        self.assertEqual(audit.operation, "repair")
        self.assertEqual(audit.provenance, "guarded_repair")
        self.assertEqual(audit.changes, {
            "title": {"old": "Natural Born Killers", "new": "Breakfast at Tiffany's"},
            "canonical_title": {"old": "", "new": "Breakfast at Tiffany's"},
        })

    def test_apply_is_idempotent(self):
        with patch(CMD) as mock_cls:
            mock_cls.return_value.details.return_value = _tmdb_details()
            self._run("--apply", "--confirm-target", _target_token())
            payload = json.loads(self._run("--apply", "--confirm-target", _target_token()))
        self.assertEqual(payload["status"], "already_correct")
        self.assertEqual(MovieIdentityAudit.objects.count(), 1)

    def test_override_row_refused(self):
        self.movie.title_is_override = True
        self.movie.save()
        with patch(CMD) as mock_cls:
            self._run("--apply", "--confirm-target", _target_token(), error="user title override")
        mock_cls.assert_not_called()
        self.movie.refresh_from_db()
        self.assertEqual(self.movie.title, "Natural Born Killers")
        self.assertEqual(MovieIdentityAudit.objects.count(), 0)

    def test_identity_mismatch_refused(self):
        with patch(CMD) as mock_cls:
            for details in (
                _tmdb_details(tmdb_id=999),
                _tmdb_details(content_type="series"),
                _tmdb_details(content_type=""),
            ):
                with self.subTest(details=details):
                    mock_cls.return_value.details.return_value = details
                    self._run("--apply", "--confirm-target", _target_token(), error="identity check failed")
        self.movie.refresh_from_db()
        self.assertEqual(self.movie.title, "Natural Born Killers")
        self.assertFalse(MovieIdentityAudit.objects.exists())

    def test_unusable_tmdb_title_refused(self):
        with patch(CMD) as mock_cls:
            mock_cls.return_value.details.return_value = _tmdb_details(title="   ")
            self._run("--apply", "--confirm-target", _target_token(), error="usable title")
        self.movie.refresh_from_db()
        self.assertEqual(self.movie.title, "Natural Born Killers")
        self.assertFalse(MovieIdentityAudit.objects.exists())

    def test_tmdb_failure_refused(self):
        with patch(CMD) as mock_cls:
            mock_cls.return_value.details.side_effect = requests.Timeout("request failed")
            self._run("--apply", "--confirm-target", _target_token(), error="Could not verify")
        self.movie.refresh_from_db()
        self.assertEqual(self.movie.title, "Natural Born Killers")
        self.assertFalse(MovieIdentityAudit.objects.exists())

    def test_apply_refuses_row_changed_during_verification(self):
        cases = (
            ({"content_type": "series"}, "identity changed"),
            ({"title": "User's title", "title_is_override": True}, "user title override"),
            ({"title": "New evidence"}, "title or year changed"),
        )
        original = Movie.objects.values().get(pk=self.movie.pk)
        for updates, message in cases:
            with self.subTest(updates=updates), patch(CMD) as mock_cls:
                self.movie.save()
                target = Movie.objects.filter(pk=self.movie.pk)

                def change_row(*args, **kwargs):
                    target.update(**updates)
                    return _tmdb_details()

                mock_cls.return_value.details.side_effect = change_row
                self._run("--apply", "--confirm-target", _target_token(), error=message)
                self.assertEqual(target.values().get(), {**original, **updates})
                self.assertFalse(MovieIdentityAudit.objects.exists())

    def test_failed_audit_rolls_back_title_repair(self):
        with patch(CMD) as mock_cls, patch.object(MovieIdentityAudit.objects, "using") as audit:
            mock_cls.return_value.details.return_value = _tmdb_details()
            audit.return_value.create.side_effect = IntegrityError("audit insert failed")
            with self.assertRaises(IntegrityError):
                self._run("--apply", "--confirm-target", _target_token())

        self.movie.refresh_from_db()
        self.assertEqual(self.movie.title, "Natural Born Killers")
        self.assertEqual(self.movie.canonical_title, "")
        self.assertFalse(MovieIdentityAudit.objects.exists())

    def test_missing_movie_refused(self):
        out = StringIO()
        with self.assertRaises(CommandError):
            call_command("repair_canonical_title", "--movie-id", "999999", stdout=out)


class RepairDatabaseAliasTests(TransactionTestCase):
    def test_every_query_uses_confirmed_alias(self):
        movie = Movie.objects.create(tmdb_id=164, title="Wrong title")
        RefreshRequest.objects.create(movie=movie)
        alias = "repair"
        default = connections["default"]
        # Use a second connection to Django's disposable test database and
        # capture each connection separately to detect accidental routing.
        alias_settings = default.settings_dict.copy()
        with (
            patch.dict(connections.databases, {alias: alias_settings}),
            patch.object(type(self), "databases", self.databases | {alias}),
        ):
            repair = connections[alias]
            try:
                args = ("repair_canonical_title", "--movie-id", str(movie.pk), "--database", alias)
                dry_run, applied = StringIO(), StringIO()
                with patch(CMD) as mock_cls, CaptureQueriesContext(default) as default_queries:
                    mock_cls.return_value.details.return_value = _tmdb_details()
                    call_command(*args, stdout=dry_run)
                    call_command(*args, "--apply", "--confirm-target", _target_token(alias), stdout=applied)

                self.assertEqual(len(default_queries), 0)
                preview = json.loads(dry_run.getvalue())
                self.assertEqual(preview["target"], _target_token(alias))
                self.assertEqual(preview["before"]["references"]["refresh_requests"], 1)
                self.assertEqual(json.loads(applied.getvalue())["status"], "repaired")
                self.assertTrue(MovieIdentityAudit.objects.using(alias).filter(movie_id=movie.pk).exists())
            finally:
                repair.close()
                del connections[alias]
