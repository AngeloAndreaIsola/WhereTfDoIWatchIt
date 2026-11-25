-- Count native season mappings once and observations once on an offline copy.
BEGIN TRANSACTION READ ONLY;

SELECT
    (SELECT count(*) FROM availability_providercontent c
     JOIN availability_source s ON s.id = c.source_id
     WHERE s.slug = 'justwatch' AND c.content_type = 'season') AS native_seasons,
    count(*) AS season_offer_rows,
    count(*) FILTER (WHERE m.id IS NULL) AS without_mapping,
    count(*) FILTER (WHERE m.tmdb_id = o.tmdb_id) AS same_numeric_target,
    count(*) FILTER (WHERE m.tmdb_id <> o.tmdb_id) AS different_numeric_target
FROM availability_offer o
JOIN availability_source s ON s.id = o.source_id
LEFT JOIN availability_contentmapping m
  ON (m.provider_id, m.provider_content_id) = (o.provider_id, o.provider_content_id)
WHERE s.slug = 'justwatch' AND o.content_type = 'season';

SELECT count(*) AS justwatch_season_mappings,
       count(*) FILTER (WHERE m.match_method = 'manual') AS manual,
       count(*) FILTER (WHERE m.match_method = 'external_id'
                         AND c.source_id = m.source_id
                         AND c.provider_id = m.provider_id
                         AND c.provider_content_id = m.provider_content_id
                         AND c.content_type = 'season'
                         AND c.metadata #>> '{external_ids,tmdb}' = m.tmdb_id::text)
           AS linked_child_id_matches_target
FROM availability_contentmapping m
JOIN availability_source s ON s.id = m.source_id
LEFT JOIN availability_providercontent c ON c.id = m.content_id
WHERE s.slug = 'justwatch' AND m.content_type = 'season';

ROLLBACK;
