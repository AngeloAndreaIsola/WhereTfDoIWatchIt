#!/usr/bin/env python3
"""Build frozen backend artifacts and execute individually approved release gates.

No command merges, pushes or starts workers. Only separately approved Gate D
runs one bounded offline reconciliation cycle. All runtime mutations require an
exact gate/project/database token. Receipts record success
only after guard, health, source isolation and database integrity checks pass.
"""
import argparse
import contextlib
import fcntl
import gzip
import hashlib
import io
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tarfile
import tempfile
import time

HERE=Path(__file__).resolve().parent
ROOT=HERE.parent.parent
if not __debug__:
    raise RuntimeError('Deployment safeguards require Python assertions enabled')
OLD='a7ce47f5a15107ea8df1c39c740e8699f0f6cd68'
FEATURE='11cc7416503c300b701945c8d39a7a2eaf6f943d'


def run(cmd, **kwargs):
    try:
        return subprocess.run([str(x) for x in cmd],check=True,capture_output=True,**kwargs).stdout
    except subprocess.CalledProcessError as error:
        detail=(error.stdout or b'')+(error.stderr or b'')
        raise RuntimeError('Command failed with exit '+str(error.returncode)+'\n'+detail.decode(errors='replace')[-6000:]) from error


def inspect(name):
    return json.loads(run(['docker','inspect',name]))[0]


def write(path,value):
    path=Path(path)
    temp=path.with_suffix(path.suffix+'.tmp')
    with temp.open('w') as stream:
        temp.chmod(0o600)
        stream.write(json.dumps(value,indent=2)+'\n')
        stream.flush()
        os.fsync(stream.fileno())
    temp.replace(path)
    # A rename is atomic, but is not a durable recovery journal until its
    # directory entry is synced too. Never advance a gate before this returns.
    descriptor=os.open(path.parent,os.O_RDONLY)
    try: os.fsync(descriptor)
    finally: os.close(descriptor)


def sha(path):
    digest=hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda:stream.read(1024*1024),b''): digest.update(block)
    return digest.hexdigest()


def verify_image_archive(path,expected_sha256,images):
    assert sha(path)==expected_sha256,'Image archive checksum mismatch'
    # Read through the gzip trailer as well as the tar members. A truncated or
    # corrupt compressed stream must fail before docker image load is attempted.
    with gzip.open(path,'rb') as stream:
        while stream.read(1024*1024): pass
    with tarfile.open(path,'r:gz') as tar:
        manifest=json.load(tar.extractfile('manifest.json'))
        configs={entry['Config'].removesuffix('.json').rsplit('/',1)[-1] for entry in manifest}
    assert {image.split(':')[1] for image in images}<=configs,'Archive lacks a pinned release image'


def archive_images(old_manifest,feature_manifest,output):
    output=Path(output).resolve()
    assert not output.exists() and not output.with_suffix(output.suffix+'.json').exists(),'Preserve existing archives'
    artifacts=[json.loads(Path(path).read_text()) for path in (old_manifest,feature_manifest)]
    assert [a['revision'] for a in artifacts]==[OLD,FEATURE]
    images=list(dict.fromkeys([a['image'] for a in artifacts]+[a['dependency_image'] for a in artifacts]))
    for image in images:
        assert json.loads(run(['docker','image','inspect',image]))[0]['Id']==image
    output.parent.mkdir(parents=True,exist_ok=True)
    incomplete=output.with_suffix(output.suffix+'.incomplete')
    with incomplete.open('xb') as raw:
        incomplete.chmod(0o600)
        with gzip.GzipFile(filename='',mode='wb',fileobj=raw,mtime=0) as compressed:
            process=subprocess.Popen(['docker','image','save',*images],stdout=subprocess.PIPE,stderr=subprocess.PIPE)
            try:
                shutil.copyfileobj(process.stdout,compressed,1024*1024)
                error=process.stderr.read()
                assert process.wait()==0,error.decode(errors='replace')
            finally:
                if process.poll() is None: process.kill(); process.wait()
                process.stdout.close(); process.stderr.close()
        raw.flush(); os.fsync(raw.fileno())
    digest=sha(incomplete)
    verify_image_archive(incomplete,digest,images)
    # A real load, without removing any existing image, verifies the recovery
    # artifact is accepted by this Docker daemon and contains the expected IDs.
    run(['docker','image','load','--input',incomplete])
    for image in images:
        assert json.loads(run(['docker','image','inspect',image]))[0]['Id']==image
    incomplete.replace(output)
    write(output.with_suffix(output.suffix+'.json'),{'file':str(output),'sha256':digest,
          'bytes':output.stat().st_size,'images':images,'artifacts':artifacts,
          'gzip_tar_validation':'PASSED','docker_load_verified':True})
    print('VERIFIED IMAGE ARCHIVE',output,digest)


def build(revision,dependency,output):
    output=Path(output).resolve()
    if output.exists(): raise RuntimeError('Preserve existing artifact manifest')
    if revision not in (OLD,FEATURE): raise RuntimeError('Only reviewed application revisions can be packaged')
    dep=json.loads(run(['docker','image','inspect',dependency]))[0]['Id']
    assert dependency==dep,'Use full dependency image ID'
    archive=run(['git','archive',revision,'backend'],cwd=ROOT)
    runtime_files=['health.py','integrity.py','check_plan.py']
    operations=hashlib.sha256(b''.join((HERE/f).read_bytes() for f in runtime_files)).hexdigest()
    recipe=sha(HERE/'Dockerfile')
    tag='wtdw-backend:'+revision[:12]+'-'+operations[:12]+'-'+recipe[:8]
    dependency_tag='wtdw-dependency:'+dep.split(':')[1]
    run(['docker','tag',dep,dependency_tag])
    with tempfile.TemporaryDirectory(prefix='wtdw-build-') as tmp:
        tmp=Path(tmp)
        with tarfile.open(fileobj=io.BytesIO(archive)) as tar: tar.extractall(tmp,filter='data')
        (tmp/'runtime').mkdir()
        for f in runtime_files: shutil.copy2(HERE/f,tmp/'runtime'/f)
        shutil.copy2(HERE/'Dockerfile',tmp/'Dockerfile')
        result=run(['docker','build','--network','none','--pull=false','--load',
                    '--build-arg','DEPENDENCY_IMAGE='+dependency_tag,'--build-arg','REVISION='+revision,
                    '--build-arg','OPERATIONS_SHA256='+operations,'-t',tag,tmp])
    image=json.loads(run(['docker','image','inspect',tag]))[0]
    output.parent.mkdir(parents=True,exist_ok=True)
    write(output,{'revision':revision,'image':image['Id'],'tag':tag,'dependency_image':dep,
                  'backend_archive_sha256':hashlib.sha256(archive).hexdigest(),'operations_sha256':operations})
    print(output.read_text())


def env_values(container):
    return dict(v.split('=',1) for v in container['Config']['Env'])


def prepare(args):
    out=Path(args.output).resolve()
    if out.exists(): raise RuntimeError('Bundle already exists; never overwrite release evidence')
    backend,db=inspect(args.backend),inspect(args.db_container)
    for c,service in [(backend,'backend'),(db,'db')]:
        assert c['Config']['Labels']['com.docker.compose.project']==args.project
        assert c['Config']['Labels']['com.docker.compose.service']==service
        assert args.network in c['NetworkSettings']['Networks']
    env=env_values(backend)
    assert env['POSTGRES_DB']==args.database and env['POSTGRES_HOST']==args.host
    assert env_values(db)['POSTGRES_DB']==args.database
    assert args.backend==args.project+'-backend-1','Use the exact Compose backend container name'
    bindings=backend['HostConfig']['PortBindings'].get('8000/tcp')
    assert bindings==[{'HostIp':'127.0.0.1','HostPort':str(args.port)}],('Published backend port would change',bindings,args.port)
    assert not env.get('UNOFFICIAL_CATALOGUE_DISCOVERY_SCHEDULES','').strip(),'Schedulers must already be disabled'
    assert re.fullmatch(r'[a-z0-9][a-z0-9_-]+',args.project)
    assert 1<=args.port<=65535
    old=json.loads(Path(args.old_image).read_text()); feature=json.loads(Path(args.feature_image).read_text())
    assert old['revision']==OLD and feature['revision']==FEATURE
    for artifact in (old,feature):
        image=json.loads(run(['docker','image','inspect',artifact['image']]))[0]
        assert image['Config']['Labels']['org.opencontainers.image.revision']==artifact['revision']
        assert image['Config']['Labels']['org.wtdw.operations.sha256']==artifact['operations_sha256']
    out.mkdir(mode=0o700,parents=True)
    # Raw env_file prevents Compose interpreting dollars/quotes in credentials.
    assert all('\n' not in k+v and '\r' not in k+v for k,v in env.items())
    (out/'backend.env').write_text(''.join(k+'='+v+'\n' for k,v in sorted(env.items())))
    (out/'backend.env').chmod(0o600)
    shutil.copy2(HERE/'compose.release.json',out/'compose.release.json')
    write(out/'manifest.json',{'project':args.project,'database':args.database,'host':args.host,
          'backend':args.backend,'db_container':args.db_container,'db_id':db['Id'],
          'postgres_image':db['Image'],
          'db_mounts':db['Mounts'],'network':args.network,'port':args.port,
          'old':old,'feature':feature,'environment_sha256':sha(out/'backend.env'),
          'compose_sha256':sha(out/'compose.release.json')})
    release=Release(out)
    release.validate_target()
    baseline=release.snapshot('old')
    assert ['tracker','0057_incremental_catalogue_reconciliation'] not in baseline['migration_history']
    assert len(baseline['tables'])==48
    write(out/'baseline.json',baseline)
    # Freeze the resolved configs as inspectable artifacts. They contain secrets.
    for version in ('old','feature'):
        config=json.loads(release.compose(version,['config','--format','json']))
        assert set(config['services'])=={'backend','migrate'}
        assert not config.get('volumes')
        for service in config['services'].values():
            assert not service.get('volumes') and not service.get('build')
            assert service['environment']['UNOFFICIAL_CATALOGUE_DISCOVERY_SCHEDULES']==''
        write(out/('resolved-'+version+'.json'),config)
    print('PREPARED',out,'No live containers changed')


class Release:
    def __init__(self,bundle):
        self.bundle=Path(bundle).resolve()
        self.m=json.loads((self.bundle/'manifest.json').read_text())
        assert sha(self.bundle/'backend.env')==self.m['environment_sha256']
        assert sha(self.bundle/'compose.release.json')==self.m['compose_sha256']

    def compose(self,version,args):
        env=dict(os.environ)
        env.update(WTDW_IMAGE=self.m[version]['image'],WTDW_BACKEND_ENV=str(self.bundle/'backend.env'),
                   WTDW_PORT=str(self.m['port']),WTDW_NETWORK=self.m['network'])
        # Explicit files, env file and project: never inherit dev overrides.
        return run(['docker','compose','--env-file','/dev/null','--project-name',self.m['project'],
                    '--file',self.bundle/'compose.release.json','--profile','ops',*args],env=env)

    def validate_target(self):
        db=inspect(self.m['db_container'])
        assert db['Id']==self.m['db_id'] and db['Mounts']==self.m['db_mounts'],'Database container/volume changed'
        assert db['Config']['Labels']['com.docker.compose.project']==self.m['project']
        assert self.m['network'] in db['NetworkSettings']['Networks']
        assert self.m['host'] in db['NetworkSettings']['Networks'][self.m['network']]['Aliases']
        active=run(['docker','ps','--filter','label=com.docker.compose.project='+self.m['project'],
                    '--format','{{.Label "com.docker.compose.service"}}']).decode().splitlines()
        assert not set(active)&{'worker','catalogue-worker','migrate'},'Stop: workers and unfinished migration/inspection runners must be absent'

    def command(self,version,arguments,extra_env=None):
        args=['run','--rm','--no-deps','-T','--entrypoint','python']
        for k,v in (extra_env or {}).items(): args+=['-e',k+'='+v]
        return self.compose(version,args+['migrate',*arguments])

    def snapshot(self,version):
        return json.loads(self.command(version,['/opt/wtdw/integrity.py','--database',self.m['database'],'--host',self.m['host']]))

    def preserved(self,version):
        state=self.snapshot(version)
        baseline=json.loads((self.bundle/'baseline.json').read_text())
        assert all(state['tables'].get(t)==v for t,v in baseline['tables'].items()),'Original catalogue data changed'
        assert set(state['tables'])-set(baseline['tables']) <= {
            'tracker_cataloguereconciliationrecord','tracker_cataloguereconciliationrun'},'Unexpected application table added'
        old_history=set(map(tuple,baseline['migration_history']))
        new_history=set(map(tuple,state['migration_history']))
        assert old_history<=new_history,'Existing migration history was removed'
        assert new_history-old_history <= {
            ('tracker','0057_incremental_catalogue_reconciliation'),
            ('tracker','0058_baseline_completed_catalogue')},'Unexpected migration history appeared'
        return state

    def check_runtime(self,version):
        c=inspect(self.m['backend'])
        assert c['Image']==self.m[version]['image']
        assert not any(m['Type']=='bind' for m in c['Mounts']),'Source mount remains'
        assert c['Config']['Entrypoint']==['/entrypoint.sh']
        assert 'gunicorn' in c['Config']['Cmd'] and '--reload' not in c['Config']['Cmd']
        assert env_values(c).get('UNOFFICIAL_CATALOGUE_DISCOVERY_SCHEDULES')==''
        assert c['State']['Health']['Status']=='healthy'
        self.command(version,['manage.py','migrate','--check'])
        self.compose(version,['exec','-T','backend','python','/opt/wtdw/health.py'])
        self.validate_target()

    def deploy(self,version):
        # Refuse incompatible images before replacing a working backend.
        self.command(version,['manage.py','migrate','--check'])
        self.compose(version,['up','--no-deps','--no-build','--pull','never','--wait','--wait-timeout','75','backend'])
        self.check_runtime(version)
        return self.preserved(version)

    def backup(self):
        dest=self.bundle/'before-migrations.dump'
        if not dest.exists():
            tmp=dest.with_suffix('.incomplete')
            with tmp.open('xb') as stream:
                subprocess.run(['docker','exec','-e','PGOPTIONS=-c default_transaction_read_only=on',self.m['db_container'],
                                'pg_dump','-Fc','-U',env_values(inspect(self.m['db_container']))['POSTGRES_USER'],
                                '-d',self.m['database']],stdout=stream,check=True)
            tmp.replace(dest)
            write(self.bundle/'backup.json',{'sha256':sha(dest),'file':dest.name})
        assert sha(dest)==json.loads((self.bundle/'backup.json').read_text())['sha256']
        verify_backup(self,dest)


def verify_backup(release,dump):
    """Restore only into newly-created, internal-network infrastructure."""
    suffix=os.urandom(5).hex(); network='wtdw-restore-'+suffix; db=network+'-db'
    created_network=created_db=False
    # Earlier private rehearsal bundles predate this field. Their exact DB
    # container ID is already pinned, so its immutable image ID is equivalent.
    postgres_image=release.m.get('postgres_image') or inspect(release.m['db_id'])['Image']
    try:
        run(['docker','network','create','--internal',network]); created_network=True
        run(['docker','run','-d','--name',db,'--network',network,'--tmpfs','/var/lib/postgresql/data',
             '-e','POSTGRES_USER=restore_test','-e','POSTGRES_PASSWORD=restore_test_only',
             '-e','POSTGRES_DB=incremental_restore_probe',postgres_image]); created_db=True
        for _ in range(40):
            if subprocess.run(['docker','exec',db,'pg_isready','-U','restore_test'],capture_output=True).returncode==0: break
            time.sleep(1)
        with Path(dump).open('rb') as stream:
            run(['docker','exec','-i',db,'pg_restore','--no-owner','--no-acl','--exit-on-error',
                 '-U','restore_test','-d','incremental_restore_probe'],stdin=stream)
        raw=run(['docker','run','--rm','--network',network,'--env-file',release.bundle/'backend.env',
                 '-e','POSTGRES_DB=incremental_restore_probe','-e','POSTGRES_HOST='+db,
                 '-e','POSTGRES_USER=restore_test','-e','POSTGRES_PASSWORD=restore_test_only',
                 '--entrypoint','python',release.m['old']['image'],'/opt/wtdw/integrity.py',
                 '--database','incremental_restore_probe','--host',db])
        state=json.loads(raw); baseline=json.loads((release.bundle/'baseline.json').read_text())
        assert state['tables']==baseline['tables'] and state['migration_history']==baseline['migration_history']
        write(release.bundle/'backup-restored.json',{'sha256':sha(dump),'all_48_tables_preserved':True,'migration_history_preserved':True})
    finally:
        if created_db: run(['docker','rm','-f',db])
        if created_network: run(['docker','network','rm',network])


def execute(args):
    release=Release(args.bundle); m=release.m
    token=args.gate+':'+m['project']+':'+m['database']+'@'+m['host']
    if args.approve!=token: raise RuntimeError('Explicit approval token required: '+token)
    # One host-wide lock per project, across all bundles. A killed process drops it.
    lock_path=Path(tempfile.gettempdir())/('wtdw-deployment-'+m['project']+'.lock')
    with lock_path.open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        release.validate_target()
        receipts=release.bundle/'receipts.json'
        journal=json.loads(receipts.read_text()) if receipts.exists() else {}
        previous=json.loads(json.dumps(journal))
        if args.gate!='rollback':
            assert previous.get('restore',{}).get('status') not in ('RUNNING','FAILED'), (
                'Finish separately approved database restore recovery before another gate')
        # Persist intent before touching containers. SIGKILL leaves RUNNING,
        # never a stale success receipt permitting the next gate.
        if args.gate=='B':
            assert previous.get('A',{}).get('status')=='PASSED','Gate A has not passed'
            assert args.quiescent==m['project']+':'+m['database']+'@'+m['host'],'Explicit exclusive maintenance confirmation required'
            journal.pop('C',None)
            journal.pop('D',None)
        elif args.gate=='C':
            assert previous.get('B',{}).get('status')=='PASSED','Gate B has not passed'
            journal.pop('D',None)
        elif args.gate=='D':
            assert previous.get('C',{}).get('status')=='PASSED','Gate C has not passed'
        elif args.gate=='A':
            assert 'B' not in previous,'A cannot regress an advanced deployment'
        else:
            assert 'A' in previous,'No source-isolation attempt to recover'
        journal[args.gate]={'status':'RUNNING','timestamp':time.time()}
        write(receipts,journal)
        try:
            if args.gate=='A':
                assert 'B' not in journal,'A cannot regress an advanced deployment'
                assert len(release.preserved('old')['tables'])==48,'Gate A requires the unchanged 0056 baseline'
                state=release.deploy('old')
            elif args.gate=='B':
                assert journal.get('A',{}).get('status')=='PASSED','Gate A has not passed'
                assert args.quiescent==m['project']+':'+m['database']+'@'+m['host'],'Explicit exclusive maintenance confirmation required'
                release.preserved('old')
                release.compose('old',['stop','backend'])
                release.backup()
                release.command('feature',['/opt/wtdw/check_plan.py'])
                release.compose('feature',['run','--rm','--no-deps','-T',
                    '-e','EXPECTED_DATABASE_NAME='+m['database'],'-e','EXPECTED_DATABASE_HOST='+m['host'],
                    '-e','CONFIRM_MIGRATION_TARGET='+m['database']+'@'+m['host'],
                    'migrate','--app-label','tracker','--target','0058_baseline_completed_catalogue'])
                state=release.deploy('old')  # Verify backward compatibility before C.
                assert state['baseline']=={'ASSOCIATED':4769,'BASELINED':326}
            elif args.gate=='C':
                assert journal.get('B',{}).get('status')=='PASSED','Gate B has not passed'
                assert release.preserved('feature')['baseline']=={'ASSOCIATED':4769,'BASELINED':326}
                state=release.deploy('feature')
            elif args.gate=='D':
                release.check_runtime('feature')
                release.preserved('feature')
                # Initial canary is explicitly offline, bounded and excludes
                # review/deferred IDs. It can never enable a recurring worker.
                report=json.loads(release.command('feature',['manage.py','reconcile_incremental_catalogue',
                    '--provider','hydrahd','--limit','1','--request-budget','1','--interval','1',
                    '--retries','0','--apply']))
                assert (report['status']=='COMPLETED' and report['mode']=='APPLY'
                        and report['selected']==0 and report['records']==[]
                        and report['offers_created']==0 and report['tmdb_budget']['attempted']==0),report
                write(release.bundle/'canary.json',report)
                state=release.preserved('feature')
            else:
                # Application-only rollback retains expanded schema and all evidence.
                recovery_before=release.preserved('old')
                state=release.deploy('old')
                assert state['tables']==recovery_before['tables'],'Application rollback changed reconciliation state'
                journal.pop('C',None)
                journal.pop('D',None)
                journal['A']={'status':'PASSED','recovered':True,'timestamp':time.time()}
            journal[args.gate]={'status':'PASSED','image':m['feature' if args.gate in ('C','D') else 'old']['image'],
                                'counts':state['counts'],'tables':state['tables'],'timestamp':time.time()}
            write(receipts,journal)
            with (release.bundle/'receipts-history.jsonl').open('a') as history:
                history.write(json.dumps({'gate':args.gate,'project':m['project'],'database':m['database'],**journal[args.gate]})+'\n')
                history.flush(); os.fsync(history.fileno())
            print('GATE',args.gate,'PASSED',m['project'],m['database']+'@'+m['host'])
        except BaseException:
            # Keep a separate failure record; never fabricate a gate success.
            journal[args.gate]={'status':'FAILED','timestamp':time.time()}
            write(receipts,journal)
            write(release.bundle/'last-failure.json',{'gate':args.gate,'timestamp':time.time(),'progression':'STOPPED'})
            raise


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    sub=parser.add_subparsers(dest='action',required=True)
    p=sub.add_parser('build'); p.add_argument('--revision',required=True); p.add_argument('--dependency-image',required=True); p.add_argument('--output',required=True)
    p=sub.add_parser('archive')
    for name in ('old-image','feature-image','output'): p.add_argument('--'+name,required=True)
    p=sub.add_parser('prepare')
    for name in ('project','backend','db-container','database','host','network','old-image','feature-image','output'): p.add_argument('--'+name,required=True)
    p.add_argument('--port',type=int,required=True)
    p=sub.add_parser('execute'); p.add_argument('--bundle',required=True); p.add_argument('--gate',choices=['A','B','C','D','rollback'],required=True)
    p.add_argument('--approve',required=True); p.add_argument('--quiescent')
    p=sub.add_parser('verify'); p.add_argument('--bundle',required=True); p.add_argument('--version',choices=['old','feature'],required=True)
    p=sub.add_parser('scheduler-readiness'); p.add_argument('--bundle',required=True)
    args=parser.parse_args()
    if args.action=='build': build(args.revision,args.dependency_image,args.output)
    elif args.action=='archive': archive_images(args.old_image,args.feature_image,args.output)
    elif args.action=='prepare': prepare(args)
    elif args.action=='execute': execute(args)
    elif args.action=='scheduler-readiness':
        release=Release(args.bundle)
        journal=json.loads((release.bundle/'receipts.json').read_text())
        assert journal.get('D',{}).get('status')=='PASSED','Gate D has not passed'
        release.check_runtime('feature'); release.preserved('feature')
        print('GATE E REVIEW ONLY: all schedules remain disabled; no worker activation is implemented. Separate approval and provider-specific activation plan required.')
    else:
        release=Release(args.bundle); release.validate_target(); release.check_runtime(args.version)
        print(json.dumps(release.preserved(args.version),indent=2))


if __name__=='__main__': main()
