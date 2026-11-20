"""Tests for the guarded canonical-title repair command (#112 remediation tooling).

Dry-run default, exact-target confirmation, independent TMDb identity check,
override refusal, idempotency, and audit recording. Offline: TMDb mocked.
"""

import json
from io import StringIO
from unittest.mock import patch

from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import connections
from django.test import TestCase

from tracker.models import Movie, MovieIdentityAudit
from tracker.services.tmdb import TMDbItem

CMD = "tracker.management.commands.repair_canonical_title.TMDbClient"


def _target_token():
    s = connections["default"].settings_dict
    return f"{s.get('NAME')}@{s.get('HOST')}"


class RepairCanonicalTitleTests(TestCase):
    def setUp(self):
        self.movie = Movie.objects.create(
            tmdb_id=164, title="Natural Born Killers", year=1961, content_type="movie",
            overview="Holly Golightly is an eccentric New York City playgirl...",
        )

    def _run(self, *extra, expect_error=False):
        out = StringIO()
        if expect_error:
            with self.assertRaises(CommandError):
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
        self._run("--apply", "--confirm-target", "wrong@target", expect_error=True)
        self.movie.refresh_from_db()
        self.assertEqual(self.movie.title, "Natural Born Killers")

    def test_apply_repairs_title_and_audits(self):
        with patch(CMD) as mock_cls:
            mock_cls.return_value.details.return_value = TMDbItem(
                tmdb_id=164, title="Breakfast at Tiffany's", year=1961, content_type="movie"
            )
            payload = json.loads(self._run("--apply", "--confirm-target", _target_token()))
        self.assertEqual(payload["status"], "repaired")
        self.movie.refresh_from_db()
        self.assertEqual(self.movie.title, "Breakfast at Tiffany's")
        self.assertEqual(self.movie.canonical_title, "Breakfast at Tiffany's")
        self.assertEqual(self.movie.tmdb_id, 164)  # identity never re-keyed
        audit = MovieIdentityAudit.objects.get(movie=self.movie)
        self.assertEqual(audit.operation, "repair")
        self.assertEqual(audit.provenance, "guarded_repair")
        self.assertEqual(audit.changes["title"]["old"], "Natural Born Killers")

    def test_apply_is_idempotent(self):
        with patch(CMD) as mock_cls:
            mock_cls.return_value.details.return_value = TMDbItem(
                tmdb_id=164, title="Breakfast at Tiffany's", year=1961, content_type="movie"
            )
            self._run("--apply", "--confirm-target", _target_token())
            payload = json.loads(self._run("--apply", "--confirm-target", _target_token()))
        self.assertEqual(payload["status"], "already_correct")
        self.assertEqual(MovieIdentityAudit.objects.count(), 1)

    def test_override_row_refused(self):
        self.movie.title_is_override = True
        self.movie.save()
        self._run("--apply", "--confirm-target", _target_token(), expect_error=True)
        self.movie.refresh_from_db()
        self.assertEqual(self.movie.title, "Natural Born Killers")
        self.assertEqual(MovieIdentityAudit.objects.count(), 0)

    def test_identity_mismatch_refused(self):
        with patch(CMD) as mock_cls:
            mock_cls.return_value.details.return_value = TMDbItem(
                tmdb_id=999, title="Something Else", year=2000, content_type="movie"
            )
            self._run("--apply", "--confirm-target", _target_token(), expect_error=True)
        self.movie.refresh_from_db()
        self.assertEqual(self.movie.title, "Natural Born Killers")

    def test_missing_movie_refused(self):
        out = StringIO()
        with self.assertRaises(CommandError):
            call_command("repair_canonical_title", "--movie-id", "999999", stdout=out)
