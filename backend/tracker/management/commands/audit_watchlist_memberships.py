"""Inventory duplicate memberships in a read-only PostgreSQL snapshot."""

import json
from datetime import datetime
from itertools import groupby
from operator import itemgetter

from django.core.management.base import BaseCommand, CommandError
from django.core.serializers.json import DjangoJSONEncoder
from django.db import connection, transaction
from django.db.models import Count, F, Window
from psycopg import sql

from tracker.models import MovieWatchEvent, UserMovieData, WatchlistItem


class ManifestEncoder(DjangoJSONEncoder):
    def default(self, value):
        if isinstance(value, datetime):
            return value.isoformat()
        return super().default(value)


def _inbound_references(cursor, member_ids):
    table = WatchlistItem._meta.db_table
    cursor.execute(
        """
        SELECT ns.nspname, rel.relname, fk.conname,
               array_agg(src.attname::text ORDER BY key.position),
               array_agg(dst.attname::text ORDER BY key.position),
               ARRAY(
                   SELECT pk_col.attname::text
                   FROM pg_constraint pk
                   CROSS JOIN LATERAL unnest(pk.conkey) WITH ORDINALITY AS k(attnum, position)
                   JOIN pg_attribute pk_col ON pk_col.attrelid = pk.conrelid AND pk_col.attnum = k.attnum
                   WHERE pk.conrelid = fk.conrelid AND pk.contype = 'p'
                   ORDER BY k.position
               )
        FROM pg_constraint fk
        JOIN pg_class rel ON rel.oid = fk.conrelid
        JOIN pg_namespace ns ON ns.oid = rel.relnamespace
        CROSS JOIN LATERAL unnest(fk.conkey, fk.confkey) WITH ORDINALITY AS key(src, dst, position)
        JOIN pg_attribute src ON src.attrelid = fk.conrelid AND src.attnum = key.src
        JOIN pg_attribute dst ON dst.attrelid = fk.confrelid AND dst.attnum = key.dst
        WHERE fk.contype = 'f' AND fk.conparentid = 0 AND fk.confrelid = to_regclass(%s)
        GROUP BY fk.oid, fk.conrelid, ns.nspname, rel.relname, fk.conname
        ORDER BY ns.nspname, rel.relname, fk.conname
        """,
        [table],
    )
    constraints = cursor.fetchall()
    references_by_member = {member_id: [] for member_id in member_ids}
    for schema, relation, name, source_cols, target_cols, pk_cols in constraints:
        if not member_ids:
            break
        columns = [sql.Identifier("ref", col) for col in (*pk_cols, *source_cols)]
        predicates = [
            sql.SQL("{} = {}").format(sql.Identifier("ref", src), sql.Identifier("member", dst))
            for src, dst in zip(source_cols, target_cols, strict=True)
        ]
        cursor.execute(
            sql.SQL("""
                SELECT member.id, {columns}
                FROM {referencing} ref JOIN {membership} member ON {predicates}
                WHERE member.id = ANY(%s)
                ORDER BY member.id, {columns}
                """).format(
                columns=sql.SQL(", ").join(columns),
                referencing=sql.Identifier(schema, relation),
                membership=sql.Identifier(table),
                predicates=sql.SQL(" AND ").join(predicates),
            ),
            [member_ids],
        )
        for member_id, *values in cursor.fetchall():
            split = len(pk_cols)
            references_by_member[member_id].append({
                "schema": schema,
                "table": relation,
                "constraint": name,
                "primary_key": dict(zip(pk_cols, values[:split], strict=True)) if pk_cols else None,
                "foreign_key": dict(zip(source_cols, values[split:], strict=True)),
            })
    return {
        "inbound_constraints": [
            {
                "schema": schema, "table": relation, "constraint": name,
                "foreign_key_columns": source_cols, "membership_columns": target_cols,
                "primary_key_columns": pk_cols,
            }
            for schema, relation, name, source_cols, target_cols, pk_cols in constraints
        ],
        "references_by_member": references_by_member,
    }


class Command(BaseCommand):
    help = "Print a read-only JSON manifest of duplicate watchlist memberships and inbound references."

    def handle(self, *args, **options):
        if connection.vendor != "postgresql":
            raise CommandError("Membership inventory requires PostgreSQL.")

        with transaction.atomic(durable=True), connection.cursor() as cursor:
            cursor.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
            # Fail if a row policy would hide records from this audit.
            cursor.execute("SET LOCAL row_security = off")
            members = list(
                WatchlistItem.objects.annotate(
                    group_size=Window(Count("pk"), partition_by=[F("user_id"), F("movie_id")]),
                )
                .filter(group_size__gt=1)
                .order_by("user_id", "movie_id", "pk")
                .values("id", "user_id", "movie_id", "source", "added_at")
            )
            inbound = _inbound_references(cursor, [row["id"] for row in members])
            groups = []
            for (owner_id, movie_id), group in groupby(members, key=itemgetter("user_id", "movie_id")):
                rows = list(group)
                for row in rows:
                    row["inbound_references"] = inbound["references_by_member"][row["id"]]
                groups.append({
                    "owner_id": owner_id,
                    "movie_id": movie_id,
                    "proposed_survivor_pk": rows[0]["id"],
                    "memberships": rows,
                    "user_movie_data": list(
                        UserMovieData.objects.filter(user_id=owner_id, movie_id=movie_id)
                        .order_by("pk").values("id", "is_watched", "rating", "watched_date", "updated_at")
                    ),
                    "movie_watch_events": list(
                        MovieWatchEvent.objects.filter(user_id=owner_id, movie_id=movie_id)
                        .order_by("pk").values("id", "rating", "watched_date", "created_at")
                    ),
                })

        self.stdout.write(json.dumps({
            "inbound_constraints": inbound["inbound_constraints"],
            "duplicate_group_count": len(groups),
            "groups": groups,
        }, cls=ManifestEncoder, indent=2))
