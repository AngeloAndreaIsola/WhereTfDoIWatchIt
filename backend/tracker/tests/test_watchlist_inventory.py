import io
import json
from datetime import date
from unittest.mock import patch

from django.core.management import call_command
from django.db import connection
from django.test import TransactionTestCase, override_settings

from tracker.management.commands import audit_watchlist_memberships as audit
from tracker.models import Movie, MovieWatchEvent, UserMovieData, WatchlistItem


@override_settings(WTDW_LEGACY_OWNER_ID=97)
class WatchlistInventoryTests(TransactionTestCase):
    def manifest(self):
        output = io.StringIO()
        call_command("audit_watchlist_memberships", stdout=output)
        return json.loads(output.getvalue())

    def test_empty_manifest(self):
        self.assertEqual(self.manifest(), {
            "inbound_constraints": [], "duplicate_group_count": 0, "groups": [],
        })

    def test_inventory_captures_duplicates_and_inbound_rows_without_mutation(self):
        movie = Movie.objects.create(tmdb_id=21, title="Indexed title")
        first = WatchlistItem.objects.create(user_id=1, movie=movie, source="letterboxd")
        second = WatchlistItem.objects.create(user_id=1, movie=movie, source="manual")
        single = WatchlistItem.objects.create(user_id=97, movie=movie, source="myanimelist")
        watched = UserMovieData.objects.create(user_id=1, movie=movie, is_watched=True, rating=4)
        event = MovieWatchEvent.objects.create(user_id=1, movie=movie, rating=4, watched_date=date(2025, 1, 2))
        UserMovieData.objects.create(user_id=97, movie=movie, is_watched=False, rating=2)
        with connection.cursor() as cursor:
            cursor.execute("""
                CREATE TABLE audit_test_membership_links (
                    tenant text NOT NULL,
                    ref_id integer NOT NULL,
                    membership bigint CONSTRAINT by_membership REFERENCES tracker_watchlistitem (id),
                    movie_id bigint,
                    source text,
                    PRIMARY KEY (tenant, ref_id),
                    CONSTRAINT by_provenance FOREIGN KEY (movie_id, source)
                        REFERENCES tracker_watchlistitem (movie_id, source)
                )
            """)
        self.addCleanup(self.drop_reference_table)
        with connection.cursor() as cursor:
            cursor.execute(
                "INSERT INTO audit_test_membership_links VALUES (%s, %s, %s, %s, %s)",
                ["fixture", 8, second.pk, movie.pk, first.source],
            )

        find_references = audit._inbound_references

        def inspect_snapshot(cursor, member_ids):
            cursor.execute("""
                SELECT current_setting('transaction_read_only'),
                       current_setting('transaction_isolation'),
                       current_setting('row_security')
            """)
            self.assertEqual(cursor.fetchone(), ("on", "repeatable read", "off"))
            return find_references(cursor, member_ids)

        with patch.object(audit, "_inbound_references", side_effect=inspect_snapshot):
            manifest = self.manifest()
        self.assertEqual(self.manifest(), manifest)
        self.assertEqual(manifest["duplicate_group_count"], 1)
        group, = manifest["groups"]
        self.assertEqual((group["owner_id"], group["movie_id"], group["proposed_survivor_pk"]), (1, movie.pk, first.pk))
        rows = group["memberships"]
        self.assertEqual(
            [(row["id"], row["source"], row["added_at"]) for row in rows],
            [(item.pk, item.source, item.added_at.isoformat()) for item in (first, second)],
        )
        for row, constraint, foreign_key in (
            (rows[0], "by_provenance", {"movie_id": movie.pk, "source": "letterboxd"}),
            (rows[1], "by_membership", {"membership": second.pk}),
        ):
            self.assertEqual(row["inbound_references"], [{
                "schema": "public",
                "table": "audit_test_membership_links",
                "constraint": constraint,
                "primary_key": {"tenant": "fixture", "ref_id": 8},
                "foreign_key": foreign_key,
            }])

        self.assertEqual(
            [(row["id"], row["is_watched"], row["rating"]) for row in group["user_movie_data"]],
            [(watched.pk, True, 4)],
        )
        self.assertEqual([row["id"] for row in group["movie_watch_events"]], [event.pk])
        self.assertCountEqual(
            WatchlistItem.objects.values_list("pk", "source", "added_at"),
            [(item.pk, item.source, item.added_at) for item in (first, second, single)],
        )
        self.assertTrue(UserMovieData.objects.get(pk=watched.pk).is_watched)
        self.assertTrue(MovieWatchEvent.objects.filter(pk=event.pk, rating=4).exists())

    def drop_reference_table(self):
        with connection.cursor() as cursor:
            cursor.execute("DROP TABLE audit_test_membership_links")
