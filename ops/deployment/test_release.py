"""Safeguard tests for the deployment controller; no Docker daemon mutations."""
import argparse
import fcntl
import gzip
import io
import json
from pathlib import Path
import subprocess
import tempfile
import tarfile
import unittest
from unittest.mock import patch
import release


class DeploymentSafeguards(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.bundle=Path(self.tmp.name)
        self.receipts=self.bundle/'receipts.json'
        self.m={'project':'wtdw-unit-safeguards','database':'incremental_test','host':'db',
                'db_container':'wtdw-unit-db','db_id':'expected','db_mounts':[],
                'old':{'image':'old-id'},'feature':{'image':'feature-id'}}
        self.fake=unittest.mock.Mock(bundle=self.bundle,m=self.m)
        self.fake.preserved.return_value={'counts':{},'tables':{},'baseline':{'ASSOCIATED':4769,'BASELINED':326}}
        self.fake.deploy.return_value=self.fake.preserved.return_value

    def args(self,gate,approve=None):
        return argparse.Namespace(bundle=str(self.bundle),gate=gate,
            approve=approve if approve is not None else gate+':wtdw-unit-safeguards:incremental_test@db',
            quiescent='wtdw-unit-safeguards:incremental_test@db')

    def call(self,args):
        with patch.object(release,'Release',return_value=self.fake): release.execute(args)

    def test_wrong_confirmation_never_touches_target(self):
        with self.assertRaises(RuntimeError): self.call(self.args('A','wrong-project'))
        self.fake.validate_target.assert_not_called()
        self.fake.deploy.assert_not_called()

    def test_B_requires_successful_A(self):
        with self.assertRaises(AssertionError): self.call(self.args('B'))
        self.fake.backup.assert_not_called()

    def test_unfinished_database_restore_blocks_all_forward_gates(self):
        for status in ('RUNNING','FAILED'):
            for gate in ('A','B','C','D'):
                release.write(self.receipts,{'A':{'status':'PASSED'},'B':{'status':'PASSED'},
                    'C':{'status':'PASSED'},'restore':{'status':status}})
                with self.assertRaisesRegex(AssertionError,'restore recovery'):
                    self.call(self.args(gate))
        self.fake.deploy.assert_not_called()
        self.fake.backup.assert_not_called()

    def test_C_requires_successful_B(self):
        release.write(self.receipts,{'A':{'status':'PASSED'},'B':{'status':'FAILED'}})
        with self.assertRaises(AssertionError): self.call(self.args('C'))
        self.fake.deploy.assert_not_called()

    def test_D_requires_successful_C(self):
        release.write(self.receipts,{'A':{'status':'PASSED'},'B':{'status':'PASSED'}})
        with self.assertRaises(AssertionError): self.call(self.args('D'))
        self.fake.command.assert_not_called()

    def test_B_requires_quiescent_confirmation(self):
        release.write(self.receipts,{'A':{'status':'PASSED'}})
        args=self.args('B'); args.quiescent=None
        with self.assertRaises(AssertionError): self.call(args)
        self.fake.compose.assert_not_called()

    def test_failed_C_replaces_stale_success(self):
        release.write(self.receipts,{'A':{'status':'PASSED'},'B':{'status':'PASSED'},'C':{'status':'PASSED'}})
        self.fake.deploy.side_effect=RuntimeError('simulated unhealthy backend')
        with self.assertRaises(RuntimeError): self.call(self.args('C'))
        self.assertEqual(json.loads(self.receipts.read_text())['C']['status'],'FAILED')
        self.assertEqual(json.loads((self.bundle/'last-failure.json').read_text())['progression'],'STOPPED')

    def test_failed_B_invalidates_C(self):
        release.write(self.receipts,{'A':{'status':'PASSED'},'B':{'status':'PASSED'},'C':{'status':'PASSED'}})
        self.fake.backup.side_effect=RuntimeError('bad restore test')
        with self.assertRaises(RuntimeError): self.call(self.args('B'))
        state=json.loads(self.receipts.read_text())
        self.assertEqual(state['B']['status'],'FAILED'); self.assertNotIn('C',state)
        self.fake.command.assert_not_called()

    def test_A_cannot_regress_an_expanded_deployment(self):
        release.write(self.receipts,{'A':{'status':'PASSED'},'B':{'status':'PASSED'}})
        with self.assertRaises(AssertionError): self.call(self.args('A'))
        self.fake.deploy.assert_not_called()

    def test_recovery_of_failed_A_is_permitted(self):
        release.write(self.receipts,{'A':{'status':'FAILED'}})
        self.call(self.args('rollback'))
        self.fake.deploy.assert_called_once_with('old')
        self.assertEqual(json.loads(self.receipts.read_text())['A']['status'],'PASSED')

    def test_project_lock_excludes_another_bundle(self):
        path=Path(tempfile.gettempdir())/'wtdw-deployment-wtdw-unit-safeguards.lock'
        with path.open('a') as lock:
            fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
            with self.assertRaises(BlockingIOError): self.call(self.args('A'))
        self.fake.validate_target.assert_not_called()

    def test_frozen_environment_tampering_refused(self):
        (self.bundle/'backend.env').write_text('POSTGRES_DB=other\n')
        release.write(self.bundle/'manifest.json',{'environment_sha256':'wrong'})
        with self.assertRaises(AssertionError): release.Release(self.bundle)

    def test_unknown_migration_history_refuses_release(self):
        release.write(self.bundle/'baseline.json',{'tables':{},'migration_history':[['tracker','0056_original']]})
        target=release.Release.__new__(release.Release); target.bundle=self.bundle
        with patch.object(target,'snapshot',return_value={'tables':{},'migration_history':[['tracker','0056_original'],['tracker','0059_unreviewed']]}):
            with self.assertRaises(AssertionError): target.preserved('feature')

    def test_removed_migration_history_refuses_release(self):
        release.write(self.bundle/'baseline.json',{'tables':{},'migration_history':[['tracker','0056_original']]})
        target=release.Release.__new__(release.Release); target.bundle=self.bundle
        with patch.object(target,'snapshot',return_value={'tables':{},'migration_history':[]}):
            with self.assertRaises(AssertionError): target.preserved('old')

    def test_database_container_replacement_refused(self):
        target=release.Release.__new__(release.Release); target.m=self.m
        with patch.object(release,'inspect',return_value={'Id':'different'}):
            with self.assertRaises(AssertionError): target.validate_target()

    def test_interrupted_migration_runner_blocks_further_operations(self):
        target=release.Release.__new__(release.Release)
        target.m={**self.m,'network':'unit-network'}
        db={'Id':'expected','Mounts':[],
            'Config':{'Labels':{'com.docker.compose.project':'wtdw-unit-safeguards'}},
            'NetworkSettings':{'Networks':{'unit-network':{'Aliases':['db']}}}}
        with patch.object(release,'inspect',return_value=db),patch.object(release,'run',return_value=b'backend\nmigrate\n'):
            with self.assertRaises(AssertionError): target.validate_target()

    def test_release_config_cannot_manage_database_or_workers(self):
        cfg=json.loads((release.HERE/'compose.release.json').read_text())
        self.assertEqual(set(cfg['services']),{'backend','migrate'})
        self.assertNotIn('volumes',cfg)
        for service in cfg['services'].values():
            self.assertNotIn('build',service); self.assertNotIn('volumes',service)
            self.assertEqual(service['environment']['UNOFFICIAL_CATALOGUE_DISCOVERY_SCHEDULES'],'')

    def test_canary_cannot_pass_on_refused_or_incomplete_work(self):
        for status in ('COOLDOWN','LOCKED','FAILED','PARTIAL'):
            release.write(self.receipts,{'C':{'status':'PASSED'}})
            self.fake.command.return_value=json.dumps({'status':status,'mode':'APPLY',
                'selected':0,'records':[],'offers_created':0,'tmdb_budget':{'attempted':0}}).encode()
            with self.assertRaises(AssertionError): self.call(self.args('D'))
            self.assertEqual(json.loads(self.receipts.read_text())['D']['status'],'FAILED')

    def test_corrupt_recovery_archive_is_refused_even_with_matching_checksum(self):
        archive=self.bundle/'images.tar.gz'
        payload=io.BytesIO()
        with tarfile.open(fileobj=payload,mode='w') as tar:
            data=json.dumps([{'Config':'a'*64+'.json'}]).encode()
            entry=tarfile.TarInfo('manifest.json'); entry.size=len(data)
            tar.addfile(entry,io.BytesIO(data))
        data=gzip.compress(payload.getvalue())
        archive.write_bytes(data[:-4])
        with self.assertRaises((EOFError,OSError)):
            release.verify_image_archive(archive,release.sha(archive),['sha256:'+'a'*64])

    def test_missing_release_image_refuses_recovery_archive(self):
        archive=self.bundle/'images.tar.gz'
        with tarfile.open(archive,'w:gz') as tar:
            data=b'[]'; entry=tarfile.TarInfo('manifest.json'); entry.size=len(data)
            tar.addfile(entry,io.BytesIO(data))
        with self.assertRaises(AssertionError):
            release.verify_image_archive(archive,release.sha(archive),['sha256:'+'a'*64])

    def test_python_optimization_fails_closed(self):
        p=subprocess.run(['python3','-O','-c','import release'],cwd=release.HERE,capture_output=True)
        self.assertNotEqual(p.returncode,0)
        self.assertIn(b'safeguards require',p.stderr)


if __name__=='__main__': unittest.main()
