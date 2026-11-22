# Watchlist membership and legacy ownership

## Current write paths

One watchlist membership represents a user and a canonical Movie PK. Movie
and series rows may share a numerical TMDb ID and still represent distinct
memberships. `WatchlistItem.source` records how the first membership was
created. Repeat actions reuse that row and its provenance.

Manual addition and ambiguity resolution already locked the Movie before
looking up a membership. Letterboxd CSV and profile imports, MyAnimeList,
canonical CSV re-import, and Google Interests used independent lookup and
insert paths. A disposable PostgreSQL transaction reproduced an import
inserting a second row while a concurrent manual addition held the Movie
lock. The Django admin could also add a second source or move a membership
without checking the user's destination title under that lock.

All application entry paths now serialize on the same Movie row. API and
import writes reuse the lowest existing PK under the lock. Admin forms
hold that lock from validation to save in the admin POST transaction and
report a duplicate as a form error. The existing schema still constrains
`(movie_id, source)`. Direct database or fixture writes remain outside the
application lock protocol until database uniqueness is established.

## Read-only inventory

The serving snapshot used for the November 20 verification contained 195
memberships, zero duplicate user and movie groups, and no Django auth
accounts. Recheck the intended database immediately before any schema work.
Use the `audit_watchlist_memberships` command for the current review manifest
and direct foreign-key inventory. See the [ownership migration plan](../watchlist-owner-migration-plan.md).

```sql
SELECT user_id, movie_id, count(*) AS memberships,
       array_agg(id ORDER BY id) AS member_ids,
       array_agg(source ORDER BY id) AS sources
FROM tracker_watchlistitem
GROUP BY user_id, movie_id
HAVING count(*) > 1
ORDER BY user_id, movie_id;
```

If duplicates already exist, helpers reuse the lowest existing PK for
stable results. They leave all historical rows, membership provenance,
watched state, ratings and watch events available for review.

## Database constraint and reconciliation under #96

The approved ownership contract targets `UNIQUE(owner_id, movie_id)` after
review and rehearsal. The installation keeps legacy owner 1 and requires
explicit approval of any future account-to-profile mapping. Dependent user
state, exports and background jobs must carry that authorized owner.

For any duplicate group, prepare a reviewed manifest with each row's ID,
source and timestamp, the exact surviving membership, and all inbound
references. Preserve the provenance of every source in durable review
evidence. Keep `UserMovieData` and `MovieWatchEvent` records separate from
membership reconciliation. Require a fresh PostgreSQL backup with a
verified checksum and successful isolated restore before any approved
live cleanup.

After approval, pause membership writers, repeat the inventory and abort
on manifest or ownership drift. Rehearse reconciled data and the chosen
database constraint in a clone. Verify each owner's memberships, source
history and watched data and exercise concurrent inserts. Commit an
explicitly approved constraint only once there are zero unresolved
duplicate groups. Reopen writes only after the matching application and
worker checks pass. Roll back with the verified pre-change restore and
matching application image while writers remain paused.
