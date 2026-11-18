#!/usr/bin/env python3
"""Rehearse release, faults and recovery on newly created infrastructure only."""
import argparse
import io
import json
import os
from pathlib import Path
import shutil
import signal
import socket
import subprocess
import tarfile
import time
import release


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    for name in ('backup','backup-sha256','old-image','feature-image','output'): parser.add_argument('--'+name,required=True)
    parser.add_argument('--smoke',action='store_true',help='Recheck final packaging through A/B/C/D and application rollback; retain separate full fault evidence')
    args=parser.parse_args(); out=Path(args.output).resolve(); out.mkdir(parents=True,exist_ok=False); out.chmod(0o700)
    backup=Path(args.backup).resolve(); assert release.sha(backup)==args.backup_sha256
    old_manifest=Path(args.old_image).resolve(); feature_manifest=Path(args.feature_image).resolve()
    old=json.loads(old_manifest.read_text())['image']; feature=json.loads(feature_manifest.read_text())['image']
    project='wtdw-rehearsal-'+str(time.time_ns()); network=project+'-internal'
    db=project+'-db-1'; backend=project+'-backend-1'; database='incremental_deployment_rehearsal'
    with socket.socket() as sock: sock.bind(('127.0.0.1',0)); port=sock.getsockname()[1]
    results={}; created_network=created_db=False

    def record(label,fn):
        start=time.monotonic()
        value=fn()
        results[label]={'status':'PASSED','seconds':round(time.monotonic()-start,2),'evidence':value}
        release.write(out/'results.json',results); print(label,'PASSED',flush=True)
        return value

    def cli(arguments,label,expected=0):
        proc=subprocess.run(['python3',str(release.HERE/'release.py'),*arguments],capture_output=True,text=True)
        (out/(label+'.log')).write_text(proc.stdout+proc.stderr)
        assert (proc.returncode==0) if expected==0 else (proc.returncode!=0),(label,proc.stdout[-2000:],proc.stderr[-3000:])
        return proc

    def prepare(name):
        bundle=out/name
        cli(['prepare','--project',project,'--backend',backend,'--db-container',db,
             '--database',database,'--host','db','--network',network,'--port',str(port),
             '--old-image',str(old_manifest),'--feature-image',str(feature_manifest),'--output',str(bundle)],name+'-prepare')
        return release.Release(bundle)

    def gate(r,name,label=None):
        arguments=['execute','--bundle',str(r.bundle),'--gate',name,'--approve',name+':'+project+':'+database+'@db']
        if name=='B': arguments+=['--quiescent',project+':'+database+'@db']
        cli(arguments,label or name+'-'+r.bundle.name)
        return r.preserved('feature' if name=='C' else 'old')['counts']

    def restore():
        # This function is deliberately unavailable in release.py. Destructive
        # fault recovery is restricted to this task's exact new container/DB.
        assert project.startswith('wtdw-rehearsal-') and db==project+'-db-1'
        c=release.inspect(db)
        assert c['Config']['Labels']['com.docker.compose.project']==project
        if subprocess.run(['docker','inspect',backend],capture_output=True).returncode==0:
            release.run(['docker','stop',backend])
        release.run(['docker','exec',db,'dropdb','--if-exists','--force','-U','rehearsal',database])
        release.run(['docker','exec',db,'createdb','-U','rehearsal',database])
        with backup.open('rb') as stream:
            release.run(['docker','exec','-i',db,'pg_restore','--no-owner','--no-acl','--exit-on-error',
                         '-U','rehearsal','-d',database],stdin=stream)

    def fault(r,label,overlay):
        faultfile=out/(label+'.json'); release.write(faultfile,{'services':{'backend':overlay}})
        try:
            r.compose('feature',['--file',str(faultfile),'up','--no-deps','--no-build','--pull','never',
                                 '--wait','--wait-timeout','12','backend'])
        except RuntimeError as error:
            (out/(label+'.log')).write_text(str(error))
        else: raise RuntimeError('Fault did not prevent health gate: '+label)
        gate(r,'rollback','rollback-'+label)
        return {'failure_rejected':True,'old_backend_healthy':True,'original_48_tables_preserved':True}

    try:
        release.run(['docker','network','create','--internal',network]); created_network=True
        release.run(['docker','run','-d','--name',db,'--network',network,'--network-alias','db',
             '--label','com.docker.compose.project='+project,'--label','com.docker.compose.service=db',
             '--tmpfs','/var/lib/postgresql/data','-e','POSTGRES_USER=rehearsal',
             '-e','POSTGRES_PASSWORD=rehearsal_only','-e','POSTGRES_DB='+database,'postgres:16-alpine']); created_db=True
        for _ in range(40):
            if subprocess.run(['docker','exec',db,'pg_isready','-U','rehearsal'],capture_output=True).returncode==0: break
            time.sleep(1)
        restore()
        source=out/'mutable-old-backend'; source.mkdir()
        with tarfile.open(fileobj=io.BytesIO(release.run(['git','archive',release.OLD,'backend'],cwd=release.ROOT))) as tar:
            tar.extractall(source,filter='data')
        config={'services':{'backend':{'image':old,'entrypoint':['/entrypoint.sh'],
            'command':['python','manage.py','runserver','0.0.0.0:8000'],
            'environment':{'POSTGRES_USER':'rehearsal','POSTGRES_PASSWORD':'rehearsal_only','POSTGRES_DB':database,
                           'POSTGRES_HOST':'db','POSTGRES_PORT':'5432','DJANGO_DEBUG':'1','DJANGO_SECRET_KEY':'rehearsal_only',
                           'DJANGO_ALLOWED_HOSTS':'localhost,127.0.0.1,backend','TMDB_API_KEY':'',
                           'UNOFFICIAL_CATALOGUE_DISCOVERY_SCHEDULES':''},
            'volumes':[str(source/'backend')+':/app'],'ports':['127.0.0.1:'+str(port)+':8000'],
            'healthcheck':{'test':['CMD','python','/opt/wtdw/health.py'],'interval':'2s','timeout':'5s','retries':10},
            'networks':['existing']}},'networks':{'existing':{'external':True,'name':network}}}
        release.write(out/'mutable-compose.json',config)
        record('01-existing-0056-api-health',lambda: release.run(['docker','compose','--project-name',project,
            '--file',out/'mutable-compose.json','up','--no-deps','--no-build','--pull','never','--wait',
            '--wait-timeout','60','backend']).decode())
        r=prepare('main-bundle')
        record('02-source-isolation-A',lambda:gate(r,'A'))
        initial=release.inspect(backend)
        (source/'backend/tracker/apps.py').write_text('raise RuntimeError("Mutable source must never reach release")\n')
        time.sleep(6)
        r.check_runtime('old')
        assert release.inspect(backend)['Id']==initial['Id'] and release.inspect(backend)['State']['StartedAt']==initial['State']['StartedAt']
        record('03-source-edit-does-not-reload',lambda:{'container_and_start_time_unchanged':True,'no_bind_mounts':True})
        record('04-migrate-B-and-old-code-on-0058',lambda:gate(r,'B'))
        record('05-feature-C-api-health',lambda:gate(r,'C'))
        initial=release.inspect(backend)
        record('06-repeat-release-idempotent',lambda:gate(r,'C','repeat-C'))
        assert release.inspect(backend)['Id']==initial['Id'] and release.inspect(backend)['State']['StartedAt']==initial['State']['StartedAt']

        replay='''import os,json
os.environ.setdefault('DJANGO_SETTINGS_MODULE','config.settings')
import django; django.setup()
from django.db import transaction
from tracker.services.incremental_catalogue_reconciliation import run_incremental_reconciliation
with transaction.atomic():
 results=[run_incremental_reconciliation('hydrahd',apply=True,allow_network=False,limit=1,request_budget=1,retries=0) for _ in range(2)]
 assert all(r['selected']==0 and r['tmdb_budget']['attempted']==0 for r in results),results
 transaction.set_rollback(True)
print(json.dumps({'selected':[r['selected'] for r in results],'http_attempts':[r['tmdb_budget']['attempted'] for r in results],'all_state_rolled_back':True}))'''
        before=r.preserved('feature')
        record('07-unchanged-reconciliation-replay',lambda:json.loads(r.command('feature',['-c',replay])))
        assert r.preserved('feature')['tables']==before['tables']
        record('07b-offline-bounded-canary-D',lambda:gate(r,'D'))
        if args.smoke:
            record('08-final-packaging-rollback',lambda:gate(r,'rollback'))
            release.write(out/'summary.json',{'status':'PASSED','project':project,'database':database,
                'network_internal':True,'packaging_smoke':True,'old_image':old,'feature_image':feature,
                'source_isolation_migrations_release_canary_and_rollback':True,'all_scenarios':len(results)})
            return
        record('08-startup-failure-and-rollback',lambda:fault(r,'startup-failure',{'command':['python','-c','raise SystemExit(23)'],'restart':'no'}))
        record('09-health-failure-and-rollback',lambda:fault(r,'health-failure',{'healthcheck':{'test':['CMD','false'],'interval':'1s','retries':1},'restart':'no'}))

        # Interrupt an actual gate after its durable intent record, before it
        # can record success. SIGKILL demonstrates crash-safe gate receipts.
        process=subprocess.Popen(['python3',str(release.HERE/'release.py'),'execute','--bundle',str(r.bundle),
             '--gate','C','--approve','C:'+project+':'+database+'@db'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,start_new_session=True)
        for _ in range(200):
            receipts=json.loads((r.bundle/'receipts.json').read_text())
            if receipts.get('C',{}).get('status')=='RUNNING': break
            time.sleep(.05)
        else: raise RuntimeError('Could not observe running gate')
        os.killpg(process.pid,signal.SIGKILL); process.wait(); time.sleep(2)
        assert json.loads((r.bundle/'receipts.json').read_text())['C']['status']=='RUNNING'
        record('10-interrupted-release-recovery',lambda:gate(r,'rollback','interrupted-rollback'))

        # Recover baseline, then fail the real Gate B after 0057, before 0058.
        record('11-restore-original-0056',lambda:restore())
        r.deploy('old'); partial=prepare('partial-bundle'); gate(partial,'A'); partial.backup()
        partial.compose('old',['stop','backend'])
        target_env={'EXPECTED_DATABASE_NAME':database,'EXPECTED_DATABASE_HOST':'db','CONFIRM_MIGRATION_TARGET':database+'@db'}
        partial.command('feature',['manage.py','guarded_migrate','--expected-database',database,'--expected-host','db',
                        '--confirm-target',database+'@db','--app-label','tracker','--target','0057_incremental_catalogue_reconciliation'])
        release.run(['docker','exec',db,'psql','-X','-U','rehearsal','-d','postgres','-v','ON_ERROR_STOP=1',
                     '-c','ALTER DATABASE '+database+' SET default_transaction_read_only = on'])
        cli(['execute','--bundle',str(partial.bundle),'--gate','B','--approve','B:'+project+':'+database+'@db',
             '--quiescent',project+':'+database+'@db'],'migration-failure',expected=1)
        assert json.loads((partial.bundle/'receipts.json').read_text())['B']['status']=='FAILED'
        cli(['execute','--bundle',str(partial.bundle),'--gate','C','--approve','C:'+project+':'+database+'@db'],
             'C-blocked-after-B-failed',expected=1)
        release.run(['docker','exec',db,'psql','-X','-U','rehearsal','-d','postgres','-v','ON_ERROR_STOP=1',
                     '-c','ALTER DATABASE '+database+' RESET default_transaction_read_only'])
        history=partial.preserved('old')['migration_history']
        assert ['tracker','0057_incremental_catalogue_reconciliation'] in history
        assert ['tracker','0058_baseline_completed_catalogue'] not in history
        try: partial.command('feature',['manage.py','migrate','--check'])
        except RuntimeError: pass
        else: raise RuntimeError('Feature guard accepted partial schema')
        record('12-migration-failure-blocks-C',lambda:{'0057_committed':True,'0058_absent':True,'B_failed':True,'C_refused':True,'feature_guard_refused':True})
        record('13-rollback-after-partial-migration',lambda:gate(partial,'rollback'))
        record('14-resume-migration-and-feature',lambda:(gate(partial,'B'),gate(partial,'C')))
        record('15-repeat-migrations',lambda:gate(partial,'B','repeat-B'))
        gate(partial,'C','C-after-repeat-B')

        def restore_recovery():
            proc=subprocess.run(['python3',str(release.HERE/'recover_database.py'),'--bundle',str(partial.bundle),
                '--approve','restore:'+project+':'+database+'@db:accept-post-backup-data-loss',
                '--quiescent',project+':'+database+'@db'],capture_output=True,text=True)
            (out/'restore-recovery.log').write_text(proc.stdout+proc.stderr)
            assert proc.returncode==0,proc.stderr[-4000:]
            return {'healthy_old_application':True,'original_database_retained':True,
                    'restore_progress':json.loads((partial.bundle/'restore-progress.json').read_text())}
        record('16-restore-based-complete-recovery',restore_recovery)
        # Reproduce the vulnerable non-atomic rename boundary using only the
        # exact clone. Preserve both names and exercise the documented resume.
        def interrupted_restore_resume():
            partial.compose('old',['stop','backend'])
            stamp=str(time.time_ns()); retained=database+'_failed_'+stamp; recovery=database+'_restore_'+stamp
            release.run(['docker','exec',db,'psql','-X','-U','rehearsal','-d','postgres','-v','ON_ERROR_STOP=1',
                         '-c','ALTER DATABASE "'+database+'" RENAME TO "'+recovery+'"'])
            # The previously retained failed DB supplies the original name
            # from a real restore; preserve it too, without dropping anything.
            status=json.loads((partial.bundle/'restore-progress.json').read_text())
            release.write(partial.bundle/'restore-progress.json',{'stage':'RESTORE_VERIFIED',
                 'recovery_database':recovery,'retained_failed_database':status['retained_failed_database']})
            proc=subprocess.run(['python3',str(release.HERE/'recover_database.py'),'--bundle',str(partial.bundle),
                 '--approve','restore:'+project+':'+database+'@db:accept-post-backup-data-loss',
                 '--quiescent',project+':'+database+'@db','--resume'],capture_output=True,text=True)
            (out/'restore-resume.log').write_text(proc.stdout+proc.stderr)
            assert proc.returncode==0,proc.stderr[-4000:]
            return {'restored_database_rename_resumed':True,'rename_committed_before_journal_update':True,
                    'failed_database_retained':True,'old_backend_healthy':True}
        record('16b-interrupted-restore-resume',interrupted_restore_resume)
        state=partial.deploy('old')
        assert len(state['tables'])==48 and ['tracker','0057_incremental_catalogue_reconciliation'] not in state['migration_history']
        release.write(out/'recovered-0056.json',state)

        # Existing strict feature validators: reverse/reapply and fixture-only
        # matching. Their only mounts are into this disposable test runner.
        common=['docker','run','--rm','--network',network,'--env-file',partial.bundle/'backend.env',
                '--entrypoint','python','-e','TMDB_API_KEY=fixture-only',
                '-v',str(release.ROOT/'scripts')+':/validation:ro','-v',str(out)+':/evidence',feature]
        for script,label in [('validate_incremental_migrations.py','migration-reverse-reapply'),
                             ('validate_incremental_catalogue.py','fixture-catalogue')]:
            raw=record('17-'+label,lambda script=script,label=label:release.run(common+['/validation/'+script,
                '--database',database,'--host','db','--output','/evidence/'+label+'.json']).decode())
        # End restored to the working old release and exact original schema.
        restore(); partial.deploy('old')
        release.write(out/'final-clone-state.json',partial.preserved('old'))
        release.write(out/'summary.json',{'status':'PASSED','project':project,'database':database,'host':'db',
            'network_internal':True,'backup_sha256':args.backup_sha256,'source_mutation_isolated':True,
            'all_scenarios':len(results),'old_code_verified_on_0056_0057_0058':True,
            'final_restored_schema':'0056','live_services_used_for_fault_tests':False})
        print('REHEARSAL PASSED',out,flush=True)
    finally:
        # Never compose down, remove volumes, or discover/delete by broad prefix.
        # A killed Docker CLI can leave its short-lived --rm inspection runner.
        for cid in release.run(['docker','ps','-aq','--filter','label=com.docker.compose.project='+project,
                                '--filter','label=com.docker.compose.service=migrate']).decode().splitlines():
            release.run(['docker','rm','-f',cid])
        if subprocess.run(['docker','inspect',backend],capture_output=True).returncode==0:
            c=release.inspect(backend)
            assert c['Config']['Labels']['com.docker.compose.project']==project
            release.run(['docker','rm','-f',backend])
        if created_db: release.run(['docker','rm','-f',db])
        if created_network: release.run(['docker','network','rm',network])

if __name__=='__main__': main()
