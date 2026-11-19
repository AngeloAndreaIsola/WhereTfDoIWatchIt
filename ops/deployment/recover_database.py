#!/usr/bin/env python3
"""Separately approved restore recovery; retain the failed database, never drop it."""
import argparse
import fcntl
import json
from pathlib import Path
import re
import tempfile
import time
import release


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--bundle',required=True); p.add_argument('--approve',required=True); p.add_argument('--quiescent',required=True)
    p.add_argument('--resume',action='store_true',help='Finish an interrupted, verified restore from its durable progress file')
    args=p.parse_args(); r=release.Release(args.bundle); m=r.m
    target=m['project']+':'+m['database']+'@'+m['host']
    if args.approve!='restore:'+target+':accept-post-backup-data-loss' or args.quiescent!=target:
        raise RuntimeError('Restore needs exact target approval and acceptance of post-backup data loss')
    assert re.fullmatch('[a-z][a-z0-9_]{0,34}',m['database'])
    with (Path(tempfile.gettempdir())/('wtdw-deployment-'+m['project']+'.lock')).open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        r.validate_target()
        dump=r.bundle/'before-migrations.dump'
        assert release.sha(dump)==json.loads((r.bundle/'backup.json').read_text())['sha256']
        release.verify_backup(r,dump)
        journal=json.loads((r.bundle/'receipts.json').read_text())
        assert 'A' in journal,'First use the source-isolation recovery procedure'
        for gate in ('B','C','D'): journal.pop(gate,None)
        journal['restore']={'status':'RUNNING'}; release.write(r.bundle/'receipts.json',journal)
        stamp=str(time.time_ns()); restored=m['database']+'_restore_'+stamp; failed=m['database']+'_failed_'+stamp
        user=release.env_values(release.inspect(m['db_container']))['POSTGRES_USER']
        def sql(statement):
            return release.run(['docker','exec',m['db_container'],'psql','-X','-U',user,'-d','postgres',
                                '-v','ON_ERROR_STOP=1','-c',statement])
        def candidate(name):
            assert re.fullmatch('[a-z][a-z0-9_]{0,62}',name)
            common=['docker','run','--rm','--network',m['network'],'--env-file',r.bundle/'backend.env',
                    '-e','POSTGRES_DB='+name,'--entrypoint','python',m['old']['image']]
            state=json.loads(release.run(common+['/opt/wtdw/integrity.py','--database',name,'--host',m['host']]))
            baseline=json.loads((r.bundle/'baseline.json').read_text())
            assert state['tables']==baseline['tables'] and state['migration_history']==baseline['migration_history']
            release.run(common+['manage.py','migrate','--check'])
        if args.resume:
            status=json.loads((r.bundle/'restore-progress.json').read_text())
            assert status['stage'] in ('RESTORE_VERIFIED','ORIGINAL_RENAMED','RESTORED_PROMOTED'),status
            restored=status['recovery_database']; failed=status['retained_failed_database']
            assert re.fullmatch(re.escape(m['database'])+'_restore_[0-9]+',restored)
            assert re.fullmatch(re.escape(m['database'])+'_failed_[0-9]+',failed)
            names=set(sql('SELECT datname FROM pg_database').decode().split())
            assert failed in names,'Retained original database is missing'
            if m['database'] not in names:
                # The first ALTER DATABASE may commit just before the host
                # process can update RESTORE_VERIFIED to ORIGINAL_RENAMED.
                # The exact retained names plus verified data resolve that
                # interruption without guessing or deleting either database.
                assert restored in names and status['stage'] in ('RESTORE_VERIFIED','ORIGINAL_RENAMED')
                candidate(restored)
                sql('ALTER DATABASE "'+restored+'" RENAME TO "'+m['database']+'"')
            else:
                assert restored not in names,'Ambiguous restore state: both database names exist'
                candidate(m['database'])
            status['stage']='RESTORED_PROMOTED'; release.write(r.bundle/'restore-progress.json',status)
            r.deploy('old')
            journal['A']={'status':'PASSED','restored':True}; journal['restore']={'status':'PASSED',**status}
            release.write(r.bundle/'receipts.json',journal)
            print('RESTORE RESUMED',target,'Previous database retained as',failed)
            return
        status={'recovery_database':restored,'retained_failed_database':failed,'stage':'PREPARED'}
        try:
            r.compose('old',['stop','backend'])
            release.run(['docker','exec',m['db_container'],'createdb','-U',user,restored])
            with dump.open('rb') as stream:
                release.run(['docker','exec','-i',m['db_container'],'pg_restore','--no-owner','--no-acl','--exit-on-error',
                             '-U',user,'-d',restored],stdin=stream)
            candidate(restored)
            status['stage']='RESTORE_VERIFIED'; release.write(r.bundle/'restore-progress.json',status)
            sql("SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname='"+m['database']+"' AND pid<>pg_backend_pid()")
            sql('ALTER DATABASE "'+m['database']+'" RENAME TO "'+failed+'"')
            status['stage']='ORIGINAL_RENAMED'; release.write(r.bundle/'restore-progress.json',status)
            sql('ALTER DATABASE "'+restored+'" RENAME TO "'+m['database']+'"')
            status['stage']='RESTORED_PROMOTED'; release.write(r.bundle/'restore-progress.json',status)
            r.deploy('old')
            journal['A']={'status':'PASSED','restored':True}; journal['restore']={'status':'PASSED',**status}
            release.write(r.bundle/'receipts.json',journal)
            print('RESTORED',target,'Previous database retained as',failed)
        except BaseException:
            journal['restore']={'status':'FAILED',**status}; release.write(r.bundle/'receipts.json',journal)
            raise

if __name__=='__main__': main()
