"""Database-enforced read-only integrity snapshot, independent of ORM version."""
import argparse
import hashlib
import json
import os
import psycopg
if not __debug__:
    raise RuntimeError('Integrity checks require Python assertions enabled')


def snapshot(database, host):
    if os.environ['POSTGRES_DB'] != database or os.environ['POSTGRES_HOST'] != host:
        raise RuntimeError('Database target differs from explicit arguments')
    with psycopg.connect(dbname=database, host=host, user=os.environ['POSTGRES_USER'],
                         password=os.environ['POSTGRES_PASSWORD'], port=os.environ.get('POSTGRES_PORT','5432'),
                         options='-c default_transaction_read_only=on') as conn, conn.cursor() as cur:
        cur.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY')
        cur.execute("SELECT current_database(),current_setting('transaction_read_only')")
        assert cur.fetchone() == (database,'on')
        cur.execute("SELECT tablename FROM pg_tables WHERE schemaname='public' AND "
                    "(tablename LIKE 'tracker_%' OR tablename LIKE 'availability_%') ORDER BY tablename")
        tables = {}
        for (table,) in cur.fetchall():
            cur.execute(psycopg.sql.SQL('SELECT to_jsonb(t)::text FROM {} t ORDER BY to_jsonb(t)::text').format(psycopg.sql.Identifier(table)))
            rows = [r[0] for r in cur.fetchall()]
            tables[table] = {'count':len(rows),'sha256':hashlib.sha256(json.dumps(rows).encode()).hexdigest()}
        counts = {key:tables[table]['count'] for key,table in {
            'discoveries':'tracker_discoveredcataloguerecord','legacy_mappings':'tracker_scrapedcontentmapping',
            'canonical_associations':'availability_contentmapping','offers':'availability_offer'}.items()}
        cur.execute('SELECT count(*) FROM tracker_discoveredcataloguerecord d WHERE NOT EXISTS '
                    '(SELECT 1 FROM tracker_canonicalbootstrapevidence e WHERE e.discovered_record_id=d.id)')
        counts['unresolved'] = cur.fetchone()[0]
        assert counts == {'discoveries':5095,'legacy_mappings':3300,'canonical_associations':4769,'unresolved':326,'offers':0},counts
        for table,keys in [('tracker_movie','tmdb_id,content_type'),('availability_contentmapping','provider_id,provider_content_id')]:
            cur.execute(f'SELECT count(*) FROM (SELECT {keys} FROM {table} GROUP BY {keys} HAVING count(*)>1) d')
            assert cur.fetchone()[0]==0,table
        cur.execute('SELECT app,name FROM django_migrations ORDER BY app,name')
        history = [list(r) for r in cur.fetchall()]
        baseline = None
        if 'tracker_cataloguereconciliationrecord' in tables:
            cur.execute('SELECT status,count(*) FROM tracker_cataloguereconciliationrecord GROUP BY status ORDER BY status')
            baseline = dict(cur.fetchall())
            assert baseline in ({}, {'ASSOCIATED':4769,'BASELINED':326}),baseline
            cur.execute('SELECT count(*) FROM tracker_cataloguereconciliationrecord WHERE attempts<>0 OR next_attempt_at IS NOT NULL')
            assert cur.fetchone()[0]==0
        return {'database':database,'host':host,'read_only':True,'counts':counts,'tables':tables,
                'migration_history':history,'baseline':baseline}


if __name__ == '__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--database',required=True)
    parser.add_argument('--host',required=True)
    args=parser.parse_args()
    print(json.dumps(snapshot(args.database,args.host),sort_keys=True))
