# Stage 2E — Title coverage and anchoring

The Stage 2D cohort contained 30 title-country cases. Its saved audit found 14
local Movie rows and 16 cases without an exact `(tmdb_id, content_type)` Movie
row. Across the 16 missing cases there are nine distinct titles; several are
missing in both IT and NL. Four NL cases nevertheless have canonical Offers.

## Why offers can exist without Movie rows

The schema supports this intentionally. `availability_offer` stores `tmdb_id`,
`content_type`, provider, country, source, and offer type; it does not require a
foreign key to `tracker_movie`. Provider catalogue content and
`ContentMapping` are also separate from the application Movie catalogue. A
source can therefore persist a canonical offer under a TMDb identity before
the local Movie materializer has a corresponding row.

The audit command makes the distinction explicit: it queries `Movie` by
`(tmdb_id, media_type)` and separately queries `Offer` by `(tmdb_id,
content_type, country)`. It can see raw offer rows while the case is still
classified `APP_DATA_MISSING`, because the application title anchor was not
found. Its fallback in-memory Movie object is used only to compute diagnostics;
it is not persisted. Therefore missing materialization blocks normal
Movie-backed catalogue/detail matching and the Stage 2C identity join, but it
does not mean those Offers are absent or unavailable.

## Four NL unanchored cases

Stage 2D found a JustWatch/Netflix subscription Offer for each of The Matrix
(TMDb 603), The Platform (619264), Stranger Things (66732), and Dark (70523),
without a matching Movie row. The Offer records are keyed independently and
remain stale. They are not proof the application can render a normal Movie
detail/catalogue entry, and their existence must not be treated as title
materialization. Do not create Movie rows automatically; establish whether
these IDs belong in the catalogue through a separately reviewed import path.

The other missing cohort entries include The Godfather, The Longest Yard,
Parasite, Coherence, and The Wire. There were no local Movie anchors for the
cohort identities. Missing coverage remains separate from availability.

## Refresh implications

Prefer anchored cohort titles for the first experiment so canonical freshness
can be observed through ordinary application lookup paths. Do not select any
of the four unanchored NL records until a reviewed materialization decision is
made. A source refresh can update/create Offer and ProviderContent evidence;
it is not a substitute for the Movie catalogue ingestion path.
