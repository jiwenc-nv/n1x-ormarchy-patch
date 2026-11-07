#!/usr/bin/env python3
"""Manage a kernel-targeted collection of local patches.

The first backend installs replacement kernel modules. Whole-kernel patches
must get their own backend before they can be registered here.
"""
import argparse
from contextlib import contextmanager
from dataclasses import dataclass
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parent
TOKEN = r'[A-Za-z0-9][A-Za-z0-9._+-]*'


class PatchError(Exception):
    pass


def read_json(path):
    try:
        data = json.loads(path.read_text())
        if not isinstance(data, dict):
            raise ValueError('expected an object')
        return data
    except (OSError, ValueError) as exc:
        raise PatchError(f'{path}: {exc}') from exc


def require(condition, message):
    if not condition:
        raise PatchError(message)


def matches(pattern, value):
    return isinstance(value, str) and re.fullmatch(pattern, value) is not None


def sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


@contextmanager
def backups():
    directory = Path(tempfile.mkdtemp(prefix='n1x-patches-backup-', dir='/var/tmp'))
    try:
        yield directory
    except BaseException:
        print(f'Override backup files retained: {directory}', file=sys.stderr)
        raise
    else:
        shutil.rmtree(directory)


@dataclass
class Patch:
    directory: Path
    spec: dict

    @property
    def id(self):
        return self.spec['id']

    @property
    def module(self):
        return self.spec['module']

    def release(self, kernel):
        return self.directory / 'release' / kernel


class System:
    modules = Path('/usr/lib/modules')
    loaded = Path('/sys/module')
    lockdown = Path('/sys/kernel/security/lockdown')
    signing = Path('/sys/module/module/parameters/sig_enforce')

    def running_kernel(self):
        return os.uname().release

    def run(self, *args):
        result = subprocess.run([str(x) for x in args], text=True,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        if result.returncode:
            raise PatchError(f'{" ".join(map(str, args))}: {result.stderr.strip()}')
        return result.stdout.strip()

    def modinfo(self, field, module, kernel):
        return self.run('modinfo', '-k', kernel, '-F', field, module)

    def target(self, patch, kernel):
        return self.modules / kernel / 'updates' / (patch.module + '.ko')

    def stock(self, patch, kernel):
        base = self.modules / kernel / patch.spec['stock_path']
        return next((Path(str(base) + suffix) for suffix in ('', '.zst', '.xz', '.gz')
                     if Path(str(base) + suffix).is_file()), None)

    def depmod(self, kernel):
        self.run('depmod', '-a', kernel)

    def refresh(self, package):
        with tempfile.NamedTemporaryFile(prefix='n1x-patches-boot-refresh-', suffix='.log',
                                         dir='/var/tmp', delete=False, mode='w+') as log:
            print(f'Refreshing boot images; output: {log.name}', flush=True)
            result = subprocess.run(['/usr/bin/limine-mkinitcpio', package],
                                    stdout=log, stderr=subprocess.STDOUT)
            log.seek(0)
            output = re.sub(r'\x1b\[[0-?]*[ -/]*[@-~]', '', log.read())
            if result.returncode or re.search(r'(?:^|\n)(?:==>\s*)?ERROR:', output):
                raise PatchError(f'Boot-image refresh failed; see {log.name}')

    @contextmanager
    def mutation_lock(self):
        with open('/run/lock/n1x-omarchy-patches.lock', 'w') as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise PatchError('Another patch operation is running') from exc
            yield


class Manager:
    def __init__(self, root=ROOT, system=None):
        self.root = root
        self.system = system or System()
        self.config = read_json(root / 'target.json')
        require(self.config.get('schema') == 1, 'Unsupported target.json schema')
        for key in ('kernel', 'kernel_package'):
            require(matches(TOKEN, self.config.get(key)), f'Invalid target {key}')
        require(matches(r'[a-f0-9]{40}', self.config.get('source_commit')),
                'Invalid target source_commit')
        ids = self.config.get('patches')
        require(isinstance(ids, list) and ids and all(matches(TOKEN, x) for x in ids),
                'target.json must register at least one patch ID')
        require(len(ids) == len(set(ids)), 'Duplicate patch IDs')
        self.patches = []
        modules = set()
        for patch_id in ids:
            directory = root / 'patches' / patch_id
            require(directory.resolve().parent == (root / 'patches').resolve(),
                    f'Patch directory escapes patches/: {patch_id}')
            spec = read_json(directory / 'patch.json')
            require(spec.get('schema') == 1 and spec.get('id') == patch_id,
                    f'{patch_id}: manifest ID/schema mismatch')
            require(spec.get('kind') == 'kernel-module',
                    f'{patch_id}: unsupported backend {spec.get("kind")!r}')
            module = spec.get('module')
            require(matches(r'[A-Za-z0-9_]+', module), f'{patch_id}: invalid module')
            require(module not in modules, f'Multiple patches replace {module}; combine them first')
            modules.add(module)
            stock = spec.get('stock_path', '')
            require(isinstance(stock, str) and stock.startswith('kernel/') and
                    '..' not in Path(stock).parts and Path(stock).name == module + '.ko',
                    f'{patch_id}: invalid stock_path')
            self.patches.append(Patch(directory, spec))
        discovered = {p.parent.name for p in (root / 'patches').glob('*/patch.json')}
        require(discovered == set(ids),
                f'Patch folders and target.json differ: {sorted(discovered.symmetric_difference(ids))}')

    @property
    def kernel(self):
        return self.config['kernel']

    def select(self, ids):
        unknown = set(ids) - {p.id for p in self.patches}
        require(not unknown, f'Unknown patches: {sorted(unknown)}')
        return [p for p in self.patches if not ids or p.id in ids]

    def source_check(self, patch):
        lock = read_json(patch.directory / 'source.lock.json')
        require(lock.get('source_commit') == self.config['source_commit'],
                'source.lock.json is out of sync with target.json source_commit')
        require((patch.directory / 'build.py').is_file(), 'Missing build.py')
        require((patch.directory / 'fix.patch').is_file(), 'Missing fix.patch')
        return lock

    def metadata(self, patch):
        info = read_json(patch.release(self.kernel) / 'build-info.json')
        require(info.get('kernel') == self.kernel, 'Release metadata targets another kernel')
        require(info.get('kernel_package') == self.config['kernel_package'],
                'Release kernel package differs from target.json')
        require(matches(r'[a-f0-9]{64}', info.get('module_sha256')), 'Invalid module checksum')
        require(matches(r'[A-F0-9]{1,24}', info.get('module_srcversion')), 'Invalid module srcversion')
        return info

    def artifact_check(self, patch):
        lock = self.source_check(patch)
        info = self.metadata(patch)
        require(info.get('upstream_commit') == self.config['source_commit'],
                'Built source commit differs from the shared target')
        require(info.get('fix_commit') == lock.get('fix_commit') and lock.get('fix_commit'),
                'Built fix commit differs from source.lock.json')
        require(info.get('patch_sha256') == sha256(patch.directory / 'fix.patch'),
                'Patch changed since release was built; rebuild required')
        for key in ('stock_text_matches_unpatched_build', 'import_symbol_crcs_match'):
            require(info.get(key) is True, f'Missing successful build check: {key}')
        artifact = patch.release(self.kernel) / (patch.module + '.ko')
        require(artifact.is_file(), f'Missing artifact: {artifact}')
        require(sha256(artifact) == info['module_sha256'], 'Artifact checksum mismatch')
        vermagic = self.system.modinfo('vermagic', artifact, self.kernel).split()
        require(vermagic and vermagic[0] == self.kernel, 'Artifact vermagic differs from target')
        require(self.system.modinfo('srcversion', artifact, self.kernel) == info['module_srcversion'],
                'Artifact srcversion differs from metadata')
        require(self.system.stock(patch, self.kernel), 'Original packaged module missing')
        return info

    def state(self, patch):
        state = {'id': patch.id, 'installed': 'absent', 'selected': False,
                 'loaded': 'not-loaded', 'errors': []}
        target = self.system.target(patch, self.kernel)
        try:
            info = self.metadata(patch)
        except PatchError as exc:
            info = None
            state['errors'].append(str(exc))
        if target.is_symlink():
            state['installed'] = 'conflict'
        elif target.exists():
            state['installed'] = ('applied' if info and target.is_file() and
                                  sha256(target) == info['module_sha256'] else 'conflict')
        try:
            selected = Path(self.system.modinfo('filename', patch.module, self.kernel))
            state['selected_path'] = str(selected)
            state['selected'] = selected.resolve() == target.resolve()
            stock = self.system.stock(patch, self.kernel)
            if stock and selected.resolve() not in (stock.resolve(), target.resolve()):
                state['errors'].append(f'Another module override is selected: {selected}')
        except PatchError as exc:
            state['errors'].append(str(exc))
        if self.system.running_kernel() != self.kernel:
            state['loaded'] = 'other-kernel'
        else:
            loaded = self.system.loaded / patch.module / 'srcversion'
            if loaded.exists():
                actual = loaded.read_text().strip()
                state['loaded_srcversion'] = actual
                state['loaded'] = ('patched' if info and actual == info['module_srcversion']
                                   else 'different')
        state['active'] = state['loaded'] == 'patched'
        state['reboot_needed'] = (self.system.running_kernel() == self.kernel and
                                  ((state['installed'] == 'applied' and not state['active']) or
                                   (state['installed'] == 'absent' and state['active'])))
        return state

    def verify(self, artifacts=True):
        errors = []
        for patch in self.patches:
            try:
                if artifacts:
                    self.artifact_check(patch)
                else:
                    self.source_check(patch)
            except (PatchError, OSError) as exc:
                errors.append(f'{patch.id}: {exc}')
        return errors

    def preflight(self, action, selected):
        # A selected apply still checks the entire registered release set.
        errors = self.verify() if action == 'apply' else []
        states = {p.id: self.state(p) for p in self.patches}
        for patch in self.patches:
            state = states[patch.id]
            if state['installed'] == 'conflict':
                errors.append(f'{patch.id}: unrelated override; refusing to replace/remove it')
            if action == 'apply' or patch in selected:
                errors.extend(f'{patch.id}: {error}' for error in state['errors'])
            if patch in selected and not self.system.stock(patch, self.kernel):
                errors.append(f'{patch.id}: original packaged module missing')
        if self.system.running_kernel() != self.kernel:
            errors.append('Apply/rollback requires the target kernel to be running')
        if action == 'apply':
            if self.system.lockdown.exists() and '[none]' not in self.system.lockdown.read_text():
                errors.append('Kernel lockdown requires trusted module signing')
            if self.system.signing.exists() and self.system.signing.read_text().strip() in ('Y', '1'):
                errors.append('Kernel enforces module signatures; unsigned artifacts cannot be applied')
        updates = self.system.modules / self.kernel / 'updates'
        if updates.is_symlink():
            errors.append('The updates directory is a symlink; refusing to write through it')
        for patch in selected:
            for suffix in ('.xz', '.gz', '.zst'):
                other = Path(str(self.system.target(patch, self.kernel)) + suffix)
                if other.exists() or other.is_symlink():
                    errors.append(f'{patch.id}: conflicting compressed override: {other}')
        pkgbase = self.system.modules / self.kernel / 'pkgbase'
        if not pkgbase.is_file() or pkgbase.read_text().strip() != self.config['kernel_package']:
            errors.append('Installed kernel package differs from target.json')
        require(not errors, '\n'.join(dict.fromkeys(errors)))
        return states

    def change(self, action, selected):
        states = self.preflight(action, selected)
        # Reapply also refreshes boot images, allowing repair after an interrupted install.
        changed = [p for p in selected if action == 'apply' or states[p.id]['installed'] == 'applied']
        if action == 'rollback':
            changed.reverse()
        if not changed:
            print('No overrides to remove. Loaded drivers are unchanged.')
            return
        package = self.config['kernel_package']
        boot_started = False
        touched = []
        with backups() as backup_dir:
            try:
                for patch in changed:
                    target = self.system.target(patch, self.kernel)
                    backup = backup_dir / (patch.module + '.ko')
                    if target.exists():
                        shutil.copy2(target, backup)
                    touched.append((target, backup))
                    if action == 'apply':
                        artifact = patch.release(self.kernel) / (patch.module + '.ko')
                        target.parent.mkdir(parents=True, exist_ok=True)
                        # Stage atomically and check the staged bytes before replacing the override.
                        fd, staged_name = tempfile.mkstemp(prefix='.n1x-', dir=target.parent)
                        os.close(fd)
                        staged = Path(staged_name)
                        try:
                            shutil.copyfile(artifact, staged)
                            require(sha256(staged) == self.metadata(patch)['module_sha256'],
                                    f'{patch.id}: staged artifact checksum changed')
                            staged.chmod(0o644)
                            staged.replace(target)
                        finally:
                            staged.unlink(missing_ok=True)
                    else:
                        target.unlink()
                self.system.depmod(self.kernel)
                for patch in changed:
                    selected_path = Path(self.system.modinfo('filename', patch.module, self.kernel)).resolve()
                    expected = (self.system.target(patch, self.kernel) if action == 'apply'
                                else self.system.stock(patch, self.kernel))
                    require(expected and selected_path == expected.resolve(),
                            f'{patch.id}: module lookup did not select the expected module')
                boot_started = True
                self.system.refresh(package)
            except (PatchError, OSError, KeyboardInterrupt) as exc:
                recovery_errors = []
                for target, backup in reversed(touched):
                    try:
                        if backup.exists():
                            shutil.copy2(backup, target)
                        else:
                            target.unlink(missing_ok=True)
                    except OSError as recovery:
                        recovery_errors.append(str(recovery))
                try:
                    self.system.depmod(self.kernel)
                    if boot_started:
                        self.system.refresh(package)
                except (PatchError, OSError) as recovery:
                    recovery_errors.append(str(recovery))
                if recovery_errors:
                    raise PatchError(f'{exc}\nRecovery incomplete: {recovery_errors}\n'
                                     'Do not reboot until module files and boot images are repaired.') from exc
                raise PatchError(f'{exc}\nPrevious override files restored; operation did not complete.') from exc
        print(f'Completed {action}: {", ".join(p.id for p in changed)}')
        print('Running drivers were not reloaded. Reboot when ready, then run ./manage.sh status.')


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['list', 'verify', 'status', 'build', 'apply',
                                            'install', 'rollback'])
    parser.add_argument('patches', nargs='*', help='Patch IDs; default: all registered patches')
    parser.add_argument('--json', action='store_true', help='JSON output for list, status, verify')
    parser.add_argument('--dry-run', action='store_true', help='Preflight apply/rollback without writes')
    parser.add_argument('--require-active', action='store_true', help='Status fails unless all selected fixes are loaded')
    parser.add_argument('--jobs', type=int, default=4, help='Parallel compiler jobs per patch')
    args = parser.parse_args(argv)
    if args.command == 'install':
        args.command = 'apply'
    require(not args.dry_run or args.command in ('apply', 'rollback'),
            '--dry-run only applies to apply/rollback')
    require(not args.require_active or args.command == 'status', '--require-active needs status')
    require(not args.json or args.command in ('list', 'status', 'verify'), '--json needs list/status/verify')
    require(args.jobs > 0, '--jobs must be positive')
    manager = Manager()
    selected = manager.select(args.patches)
    if args.command == 'list':
        rows = [p.spec for p in selected]
        if args.json:
            print(json.dumps({'target': manager.config, 'patches': rows}, indent=2))
        else:
            print(f'Target: {manager.kernel} ({manager.config["kernel_package"]})')
            for row in rows:
                print(f'{row["id"]}: {row.get("description", row["module"])}')
    elif args.command == 'verify':
        errors = manager.verify()
        if args.json:
            print(json.dumps({'kernel': manager.kernel, 'ok': not errors, 'errors': errors}, indent=2))
        else:
            print(f'Verifying all {len(manager.patches)} patches for {manager.kernel}')
            print('\n'.join(errors) if errors else 'All source pins and release artifacts are in sync.')
        return 1 if errors else 0
    elif args.command == 'status':
        rows = [manager.state(p) for p in selected]
        if args.json:
            print(json.dumps({'target_kernel': manager.kernel,
                              'running_kernel': manager.system.running_kernel(), 'patches': rows}, indent=2))
        else:
            print(f'Target: {manager.kernel}; running: {manager.system.running_kernel()}')
            for row in rows:
                print(f'{row["id"]}: disk={row["installed"]}, '
                      f'selected={row["selected"]}, loaded={row["loaded"]}'
                      + ('; reboot needed' if row['reboot_needed'] else ''))
                for error in row['errors']:
                    print(f'  {error}')
        if any(r['errors'] or r['installed'] == 'conflict' or
               (r['installed'] == 'applied') != r['selected'] for r in rows):
            return 1
        return 2 if args.require_active and not all(r['active'] for r in rows) else 0
    elif args.command == 'build':
        require(os.geteuid() != 0, 'Build as your normal user, not root')
        errors = manager.verify(artifacts=False)
        require(not errors, '\n'.join(errors))
        for patch in selected:
            require(not patch.release(manager.kernel).exists(),
                    f'{patch.id}: release already exists; retain its rollback metadata before rebuilding')
        for patch in selected:
            subprocess.run([sys.executable, str(patch.directory / 'build.py'),
                            '--kernel', manager.kernel, '--kernel-package', manager.config['kernel_package'],
                            '--jobs', str(args.jobs)], check=True)
            manager.artifact_check(patch)
    else:
        if args.dry_run:
            manager.preflight(args.command, selected)
            print(f'Preflight passed: {args.command} {", ".join(p.id for p in selected)} for {manager.kernel}')
            print('No files or boot images changed. A real operation also requires root and boot-image tools.')
        elif os.geteuid() != 0:
            # Validate before elevation; the elevated process validates again under the lock.
            manager.preflight(args.command, selected)
            os.execvp('sudo', ['sudo', '--', sys.executable, str(ROOT / 'patchctl.py'),
                               *(sys.argv[1:] if argv is None else argv)])
        else:
            require(shutil.which('depmod'), 'depmod is required')
            require(Path('/usr/bin/limine-mkinitcpio').is_file(), 'limine-mkinitcpio is required')
            with manager.system.mutation_lock():
                manager.change(args.command, selected)
    return 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except (PatchError, OSError, subprocess.CalledProcessError) as exc:
        print(f'Patch manager: {exc}', file=sys.stderr)
        sys.exit(1)
