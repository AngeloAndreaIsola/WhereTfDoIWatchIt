#!/usr/bin/env python3
"""Run both reported focused baselines and the full suite on disposable PostgreSQL."""
import argparse
import json
from pathlib import Path
import re
import subprocess
import time
import release

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--image-manifest',required=True); p.add_argument('--output',required=True)
    a=p.parse_args(); out=Path(a.output).resolve(); out.mkdir(parents=True,exist_ok=False)
    image=json.loads(Path(a.image_manifest).read_text())['image']
    name='wtdw-tests-'+str(time.time_ns()); db=name+'-db'; results={}
    created_network=created_db=False
    try:
        release.run(['docker','network','create','--internal',name]); created_network=True
        release.run(['docker','run','-d','--name',db,'--network',name,'--tmpfs','/var/lib/postgresql/data',
                     '-e','POSTGRES_USER=test','-e','POSTGRES_PASSWORD=test_only','-e','POSTGRES_DB=incremental_tests',
                     'postgres:16-alpine']); created_db=True
        for _ in range(40):
            if subprocess.run(['docker','exec',db,'pg_isready','-U','test'],capture_output=True).returncode==0: break
            time.sleep(1)
        base=['docker','run','--rm','--network',name,'--entrypoint','python',
              '-e','POSTGRES_HOST='+db,'-e','POSTGRES_DB=incremental_tests',
              '-e','POSTGRES_USER=test','-e','POSTGRES_PASSWORD=test_only','-e','DJANGO_DEBUG=1',
              '-e','DJANGO_SECRET_KEY=test_only','-e','UNOFFICIAL_CATALOGUE_DISCOVERY_SCHEDULES=',
              '-e','TMDB_API_KEY=','-e','RAPIDAPI_KEY=','-e','WATCHMODE_API_KEY=',
              '-e','PYTHONPATH=/app/scripts','-v',str(release.ROOT/'scripts')+':/app/scripts:ro']
        for f in ('docker-compose.yml','docker-compose.test.yml'): base+=['-v',str(release.ROOT/f)+':/app/'+f+':ro']
        base+=[image]
        focused=['tracker.tests.test_incremental_catalogue_reconciliation','tracker.tests.test_catalogue_discovery_scheduler',
                 'tracker.tests.test_catalogue_reconciliation','tracker.tests.test_canonical_bootstrap',
                 'tracker.tests.test_canonical_rollout','tracker.tests.test_catalogue_discovery']
        expanded=focused+['tracker.tests.test_catalogue_identity','tracker.tests.test_canonical_identity',
                          'tracker.tests.test_kisskh_catalogue_discovery','availability.tests.test_candidate_gateway']
        checks=[('focused-160',['manage.py','test',*expanded,'--noinput'],160),
                ('focused-112',['manage.py','test',*focused,'--noinput'],112),
                ('backend-3382',['manage.py','test','--noinput'],3382),
                ('migration-consistency',['manage.py','makemigrations','--check','--dry-run'],None),
                ('django-check',['manage.py','check'],None)]
        for label,args,count in checks:
            start=time.monotonic()
            with (out/(label+'.log')).open('w') as log: proc=subprocess.run(base+args,stdout=log,stderr=subprocess.STDOUT)
            text=(out/(label+'.log')).read_text()
            actual=re.search(r'Ran (\d+) tests? in',text)
            results[label]={'exit_code':proc.returncode,'count':int(actual[1]) if actual else None,'seconds':round(time.monotonic()-start,2)}
            release.write(out/'results.json',results)
            print(label,results[label],flush=True)
            assert proc.returncode==0,text[-4000:]
            if count: assert actual and int(actual[1])==count,'Reported baseline test count changed'
    finally:
        if created_db: release.run(['docker','rm','-f',db])
        if created_network: release.run(['docker','network','rm',name])

if __name__=='__main__': main()
