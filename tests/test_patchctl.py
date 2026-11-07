"""Exercise install/rollback decisions against a disposable fake system."""
from contextlib import contextmanager
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch as mock_patch

import patchctl


class FakeSystem(patchctl.System):
    def __init__(self, root):
        self.modules = root / 'modules'
        self.loaded = root / 'loaded'
        self.lockdown = root / 'lockdown'
        self.signing = root / 'sig_enforce'
        self.kernel = '7.3.0-test'
        self.events = []
        self.refresh_failures = 0
        self.depmod_failures = 0
        self.wrong_lookup = False

    def running_kernel(self):
        return self.kernel

    def modinfo(self, field, module, kernel):
        if field == 'vermagic':
            return kernel + ' SMP modversions'
        if field == 'srcversion':
            return 'ABCD1234'
        if field == 'filename':
            override = self.modules / kernel / 'updates' / (str(module) + '.ko')
            if override.exists() and not self.wrong_lookup:
                return str(override)
            return str(self.modules / kernel / 'kernel' / (str(module) + '.ko'))
        raise AssertionError(field)

    def depmod(self, kernel):
        self.events.append(('depmod', kernel))
        if self.depmod_failures:
            self.depmod_failures -= 1
            raise patchctl.PatchError('injected depmod failure')

    def refresh(self, package):
        self.events.append(('refresh', package))
        if self.refresh_failures:
            self.refresh_failures -= 1
            raise patchctl.PatchError('injected boot-image failure')


class ManagerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.system = FakeSystem(self.root / 'system')
        self.target = {'schema': 1, 'kernel': self.system.kernel,
                       'kernel_package': 'linux-omarchy', 'source_commit': 'a' * 40,
                       'patches': ['alpha', 'beta']}
        self.write_json(self.root / 'target.json', self.target)
        for name in self.target['patches']:
            directory = self.root / 'patches' / name
            self.write_json(directory / 'patch.json', {
                'schema': 1, 'id': name, 'kind': 'kernel-module',
                'module': name, 'stock_path': f'kernel/{name}.ko'})
            self.write_json(directory / 'source.lock.json', {
                'source_commit': 'a' * 40, 'fix_commit': 'b' * 40})
            (directory / 'build.py').write_text('# fake builder\n')
            (directory / 'fix.patch').write_text('fake patch\n')
            release = directory / 'release' / self.system.kernel
            release.mkdir(parents=True)
            module = release / (name + '.ko')
            module.write_bytes(('patched-' + name).encode())
            self.write_json(release / 'build-info.json', {
                'kernel': self.system.kernel, 'kernel_package': 'linux-omarchy',
                'upstream_commit': 'a' * 40, 'fix_commit': 'b' * 40,
                'module_sha256': patchctl.sha256(module), 'module_srcversion': 'ABCD1234',
                'patch_sha256': patchctl.sha256(directory / 'fix.patch'),
                'stock_text_matches_unpatched_build': True, 'import_symbol_crcs_match': True})
            stock = self.system.modules / self.system.kernel / 'kernel' / (name + '.ko')
            stock.parent.mkdir(parents=True, exist_ok=True)
            stock.write_bytes(('stock-' + name).encode())
        self.manager = patchctl.Manager(self.root, self.system)
        (self.system.modules / self.manager.kernel / 'pkgbase').write_text('linux-omarchy\n')

        @contextmanager
        def local_backups():
            with tempfile.TemporaryDirectory(dir=self.root) as directory:
                yield Path(directory)
        self.backups_mock = mock_patch('patchctl.backups', local_backups)
        self.backups_mock.start()
        self.addCleanup(self.backups_mock.stop)

    @staticmethod
    def write_json(path, data):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data))

    def target_file(self, index=0):
        return self.system.target(self.manager.patches[index], self.manager.kernel)

    def apply(self):
        self.manager.change('apply', self.manager.patches)

    def test_verify_and_apply_then_rollback(self):
        self.assertEqual(self.manager.verify(), [])
        self.apply()
        self.assertEqual(self.system.events, [('depmod', self.manager.kernel),
                                              ('refresh', 'linux-omarchy')])
        self.assertTrue(all(self.manager.state(p)['installed'] == 'applied'
                            for p in self.manager.patches))
        self.manager.change('rollback', self.manager.patches)
        self.assertFalse(self.target_file().exists())
        self.assertFalse(self.target_file(1).exists())
        self.assertEqual(len(self.system.events), 4)
        for p in self.manager.patches:
            self.assertEqual(self.system.stock(p, self.manager.kernel).read_bytes(),
                             ('stock-' + p.module).encode())

    def test_loaded_and_disk_state_are_independent(self):
        self.apply()
        p = self.manager.patches[0]
        state = self.manager.state(p)
        self.assertTrue(state['reboot_needed'])
        self.assertFalse(state['active'])
        loaded = self.system.loaded / p.module / 'srcversion'
        loaded.parent.mkdir(parents=True)
        loaded.write_text('ABCD1234\n')
        self.assertTrue(self.manager.state(p)['active'])
        self.assertFalse(self.manager.state(p)['reboot_needed'])
        self.manager.change('rollback', [p])
        self.assertTrue(self.manager.state(p)['active'])
        self.assertTrue(self.manager.state(p)['reboot_needed'])

    def test_partial_selection_still_checks_other_artifacts(self):
        p = self.manager.patches[1]
        (p.release(self.manager.kernel) / 'beta.ko').unlink()
        with self.assertRaisesRegex(patchctl.PatchError, 'Missing artifact'):
            self.manager.change('apply', [self.manager.patches[0]])
        self.assertFalse(self.target_file().exists())
        self.assertEqual(self.system.events, [])

    def test_modified_patch_refuses_apply(self):
        (self.manager.patches[1].directory / 'fix.patch').write_text('new patch')
        with self.assertRaisesRegex(patchctl.PatchError, 'Patch changed'):
            self.apply()
        self.assertFalse(self.target_file().exists())

    def test_out_of_sync_source_lock(self):
        self.write_json(self.manager.patches[1].directory / 'source.lock.json',
                        {'source_commit': 'c' * 40, 'fix_commit': 'b' * 40})
        self.assertIn('out of sync', '\n'.join(self.manager.verify()))

    def test_wrong_artifact_kernel(self):
        p = self.manager.patches[1].release(self.manager.kernel) / 'build-info.json'
        info = patchctl.read_json(p)
        info['kernel'] = 'other'
        self.write_json(p, info)
        with self.assertRaisesRegex(patchctl.PatchError, 'another kernel'):
            self.apply()

    def test_checksum_tampering(self):
        p = self.manager.patches[0]
        (p.release(self.manager.kernel) / 'alpha.ko').write_bytes(b'corrupt')
        with self.assertRaisesRegex(patchctl.PatchError, 'checksum mismatch'):
            self.apply()

    def test_conflicting_override_is_preserved(self):
        target = self.target_file(1)
        target.parent.mkdir(parents=True)
        target.write_bytes(b'someone elses module')
        for command in ['apply', 'rollback']:
            with self.assertRaisesRegex(patchctl.PatchError, 'unrelated override'):
                self.manager.change(command, self.manager.patches)
        self.assertEqual(target.read_bytes(), b'someone elses module')
        self.assertFalse(self.target_file().exists())

    def test_symlink_override_rejected(self):
        target = self.target_file()
        target.parent.mkdir(parents=True)
        target.symlink_to(self.system.stock(self.manager.patches[0], self.manager.kernel))
        with self.assertRaisesRegex(patchctl.PatchError, 'unrelated override'):
            self.apply()

    def test_compressed_override_is_preserved(self):
        other = Path(str(self.target_file()) + '.zst')
        other.parent.mkdir(parents=True)
        other.write_bytes(b'unrelated compressed override')
        with self.assertRaisesRegex(patchctl.PatchError, 'conflicting compressed override'):
            self.apply()
        self.assertTrue(other.exists())
        self.assertFalse(self.target_file().exists())

    def test_package_mismatch_refuses_apply(self):
        (self.system.modules / self.manager.kernel / 'pkgbase').write_text('other-package')
        with self.assertRaisesRegex(patchctl.PatchError, 'Installed kernel package differs'):
            self.apply()

    def test_wrong_running_kernel_refuses_mutation(self):
        self.system.kernel = '7.4.0-new'
        self.assertEqual(self.manager.state(self.manager.patches[0])['loaded'], 'other-kernel')
        with self.assertRaisesRegex(patchctl.PatchError, 'target kernel to be running'):
            self.apply()

    def test_lockdown_and_signature_enforcement(self):
        self.system.lockdown.write_text('none [integrity] confidentiality')
        with self.assertRaisesRegex(patchctl.PatchError, 'lockdown'):
            self.apply()
        self.system.lockdown.write_text('[none] integrity confidentiality')
        self.system.signing.write_text('Y')
        with self.assertRaisesRegex(patchctl.PatchError, 'enforces module signatures'):
            self.apply()

    def test_depmod_failure_restores_all_files(self):
        self.system.depmod_failures = 1
        with self.assertRaisesRegex(patchctl.PatchError, 'Previous override files restored'):
            self.apply()
        self.assertFalse(self.target_file().exists())
        self.assertFalse(self.target_file(1).exists())
        self.assertEqual([e[0] for e in self.system.events], ['depmod', 'depmod'])

    def test_refresh_failure_restores_preexisting_and_missing_overrides(self):
        self.manager.change('apply', [self.manager.patches[0]])
        before = self.target_file().read_bytes()
        self.system.events.clear()
        self.system.refresh_failures = 1
        with self.assertRaisesRegex(patchctl.PatchError, 'Previous override files restored'):
            self.apply()
        self.assertEqual(self.target_file().read_bytes(), before)
        self.assertFalse(self.target_file(1).exists())
        self.assertEqual([e[0] for e in self.system.events],
                         ['depmod', 'refresh', 'depmod', 'refresh'])

    def test_rollback_refresh_failure_reinstates_overrides(self):
        self.apply()
        self.system.refresh_failures = 1
        with self.assertRaisesRegex(patchctl.PatchError, 'Previous override files restored'):
            self.manager.change('rollback', self.manager.patches)
        self.assertTrue(self.target_file().exists())
        self.assertTrue(self.target_file(1).exists())

    def test_recovery_failure_is_explicit(self):
        self.system.refresh_failures = 2
        with self.assertRaisesRegex(patchctl.PatchError, 'Recovery incomplete'):
            self.apply()

    def test_lookup_failure_restores_files_before_boot_refresh(self):
        self.system.wrong_lookup = True
        with self.assertRaisesRegex(patchctl.PatchError, 'module lookup'):
            self.apply()
        self.assertFalse(self.target_file().exists())
        self.assertNotIn('refresh', [e[0] for e in self.system.events])

    def test_rollback_does_not_require_artifact_or_current_source(self):
        self.apply()
        p = self.manager.patches[0]
        (p.release(self.manager.kernel) / 'alpha.ko').unlink()
        (p.directory / 'fix.patch').unlink()
        self.manager.change('rollback', [p])
        self.assertFalse(self.target_file().exists())

    def test_unregistered_patch_and_duplicate_modules_are_rejected(self):
        path = self.root / 'patches' / 'gamma' / 'patch.json'
        self.write_json(path, {})
        with self.assertRaisesRegex(patchctl.PatchError, 'folders and target.json differ'):
            patchctl.Manager(self.root, self.system)
        path.unlink()
        p = self.root / 'patches' / 'beta' / 'patch.json'
        spec = patchctl.read_json(p)
        spec.update(module='alpha', stock_path='kernel/alpha.ko')
        self.write_json(p, spec)
        with self.assertRaisesRegex(patchctl.PatchError, 'Multiple patches replace'):
            patchctl.Manager(self.root, self.system)


if __name__ == '__main__':
    unittest.main()
