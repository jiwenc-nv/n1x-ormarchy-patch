#!/usr/bin/env python3
"""Build the opt-in P16 sleep experiment locally. Never installs or edits boot."""
import argparse
import datetime
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parent
LOCK = json.loads((ROOT / 'source.lock.json').read_text())
CACHE = ROOT / '.cache' / 'baseline'
TOOLS = ROOT / '.cache' / 'tools'
WORK = ROOT / 'build'
KERNEL = WORK / 'linux-7.3'
NVIDIA = WORK / 'nvidia'
DRIVER = Path('drivers/soc/mediatek/power_wrap/power_wrap_lps0.c')
WIFI = ROOT.parent / 'wifi-mt7925-reset' / 'fix.patch'


def sha(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def check(path, expected):
    if not path.is_file() or sha(path) != expected:
        raise RuntimeError(f'Missing or changed input: {path}')


def run(args, cwd=ROOT, **kwargs):
    return subprocess.run([str(a) for a in args], cwd=cwd, check=True, **kwargs)


def fetch(path, url, expected):
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(path.name + '.part')
        run(['curl', '-fL', '--retry', '3', '-o', temporary, url])
        check(temporary, expected)
        temporary.rename(path)
    check(path, expected)


def environment():
    env = os.environ.copy()
    for name in ('CFLAGS', 'CXXFLAGS', 'LDFLAGS', 'MAKEFLAGS', 'KERNELRELEASE',
                 'ARCH', 'CROSS_COMPILE', 'KBUILD_OUTPUT', 'LLVM', 'LOCALVERSION'):
        env.pop(name, None)
    prefix = TOOLS / 'root' / 'usr'
    env.update(PATH=str(prefix / 'bin') + ':' + env['PATH'],
               LD_LIBRARY_PATH=str(prefix / 'lib'),
               KBUILD_BUILD_USER='linux-omarchy', KBUILD_BUILD_HOST='omarchy',
               KBUILD_BUILD_VERSION='1',
               KBUILD_BUILD_TIMESTAMP=datetime.datetime.fromtimestamp(
                   LOCK['source_date_epoch'], datetime.timezone.utc).strftime(
                       '%a, %d %b %Y %H:%M:%S +0000'),
               SOURCE_DATE_EPOCH=str(LOCK['source_date_epoch']))
    return env


def tools():
    destination = TOOLS / 'root'
    destination.mkdir(parents=True, exist_ok=True)
    for item in LOCK['local_tools']:
        package = TOOLS / item['filename']
        fetch(package, item['url'], item['sha256'])
        sig = Path(str(package) + '.sig')
        if not sig.exists():
            run(['curl', '-fL', '--retry', '3', '-o', sig, item['url'] + '.sig'])
        run(['pacman-key', '--verify', sig, package])
        run(['bsdtar', '-xf', package, '-C', destination])
    run(['rustc', '--version', '--verbose'], env=environment())
    run(['bc', '--version'], env=environment())


def inputs():
    check(ROOT / 'fix.patch', LOCK['candidate_patch_sha256'])
    check(WIFI, LOCK['existing_wifi_patch_sha256'])
    for item in LOCK['archives']:
        fetch(CACHE / item['filename'], item['url'], item['sha256'])
    recipe = CACHE / 'recipe'
    if not recipe.exists():
        recipe.mkdir()
        subtree = ('omarchy-pkgs-' + LOCK['recipe_commit'] +
                   '/pkgbuilds/linux-omarchy-aarch64')
        run(['bsdtar', '-xf', CACHE / 'omarchy-pkgs.tar.gz', '-C', recipe,
             '--strip-components', '3', subtree])
    check(recipe / 'PKGBUILD', LOCK['recipe_sha256'])
    check(recipe / 'config.aarch64', LOCK['installed_config_sha256'])
    records = json.loads((ROOT / 'provenance/source-checksums.json').read_text())
    for item in records:
        # Signature files were not recipe checksum inputs. Archive/input hashes
        # here are pinned to the recipe from the authenticated installed package.
        if not item['recipe_checksum_checked']:
            continue
        path = CACHE / item['name']
        if not path.exists():
            path = recipe / item['name']
        check(path, item['sha256'])
    return recipe


def apply(patch, directory, log):
    with patch.open('rb') as stream:
        run(['patch', '--batch', '--fuzz=0', '-Np1'], cwd=directory,
            stdin=stream, stdout=log, stderr=subprocess.STDOUT)


def prepare():
    recipe = inputs()
    if KERNEL.exists() or NVIDIA.exists():
        raise RuntimeError('build source already exists; reuse it with build, '
                           'or rename build/ before preparing again')
    WORK.mkdir(exist_ok=True)
    with (WORK / 'prepare.log').open('w') as log:
        run(['bsdtar', '-xf', CACHE / 'linux-7.3.tar.xz', '-C', WORK])
        patches = json.loads((ROOT / 'provenance/kernel-patch-order.json').read_text())
        if len(patches) != LOCK['kernel_patches_applied']:
            raise RuntimeError('Unexpected vendor patch count')
        for name in patches:
            apply(recipe / name, KERNEL, log)
        check(KERNEL / DRIVER, LOCK['baseline_driver_sha256'])
        shutil.copy2(recipe / 'config.aarch64', KERNEL / '.config')
        (KERNEL / 'localversion.10-pkgrel').write_text('-26\n')
        (KERNEL / 'localversion.20-pkgname').write_text('-omarchy-p16-sleep1\n')
        apply(WIFI, KERNEL / 'drivers/net/wireless/mediatek/mt76', log)
        apply(ROOT / 'fix.patch', KERNEL, log)
        check(KERNEL / DRIVER, LOCK['candidate_driver_sha256'])
        # Copy the already installed, patched driver source; do not run DKMS
        # against the live system. Record every copied file for later auditing.
        installed = Path('/usr/src') / ('nvidia-' + LOCK['nvidia_version'])
        shutil.copytree(installed, NVIDIA, symlinks=True)
        manifest = {str(p.relative_to(NVIDIA)): sha(p) for p in sorted(NVIDIA.rglob('*'))
                    if p.is_file() and not p.is_symlink()}
        (WORK / 'nvidia-source-checksums.json').write_text(json.dumps(manifest, indent=2)+'\n')
        run(['make', 'olddefconfig'], cwd=KERNEL, env=environment(),
            stdout=log, stderr=subprocess.STDOUT)
    check(KERNEL / '.config', LOCK['installed_config_sha256'])
    release = subprocess.check_output(['make', '-s', 'kernelrelease'], cwd=KERNEL,
                                      env=environment(), text=True).strip()
    if release != LOCK['test_kernel']:
        raise RuntimeError(f'Unexpected kernel release: {release}')
    (WORK / 'prepared.json').write_text(json.dumps({
        'kernel': release, 'source_lock_sha256': sha(ROOT / 'source.lock.json'),
        'config_sha256': sha(KERNEL / '.config')}, indent=2)+'\n')
    print('Prepared', release, 'with byte-identical kernel config', flush=True)


def build(jobs):
    prepared = json.loads((WORK / 'prepared.json').read_text())
    check(ROOT / 'source.lock.json', prepared['source_lock_sha256'])
    check(KERNEL / '.config', LOCK['installed_config_sha256'])
    check(KERNEL / DRIVER, LOCK['candidate_driver_sha256'])
    run([sys.executable, ROOT / 'tests/test_lps0_timing.py', KERNEL])
    for name, cwd, command in (
        ('kernel', KERNEL, ['make', f'-j{jobs}', 'Image', 'modules']),
        ('nvidia', NVIDIA, ['make', f'-j{jobs}', f'SYSSRC={KERNEL}',
                            f'SYSOUT={KERNEL}', 'IGNORE_PREEMPT_RT_PRESENCE=1', 'modules'])):
        print(f'Building {name}; output: {WORK / (name + ".log")}', flush=True)
        with (WORK / (name + '.log')).open('a') as log:
            run(['nice', '-n', '10', *command], cwd=cwd, env=environment(),
                stdout=log, stderr=subprocess.STDOUT)
    check(KERNEL / '.config', LOCK['installed_config_sha256'])
    # Check the real composite object's parameter prefix, not just C mocks.
    data = subprocess.check_output(['strings', KERNEL / 'vmlinux'])
    if b'power_wrap_drv.p16_lps0_late' not in data:
        raise RuntimeError('Expected built-in kernel parameter is missing')
    artifacts = [KERNEL / 'arch/arm64/boot/Image', KERNEL / 'vmlinux',
                 KERNEL / 'System.map', KERNEL / 'Module.symvers']
    artifacts += sorted(NVIDIA.glob('kernel-open/*.ko'))
    if len(list(NVIDIA.glob('kernel-open/*.ko'))) != 5:
        raise RuntimeError('Expected five NVIDIA kernel modules')
    for module in NVIDIA.glob('kernel-open/*.ko'):
        vermagic = subprocess.check_output(['modinfo', '-F', 'vermagic', module], text=True)
        if vermagic.split()[0] != LOCK['test_kernel']:
            raise RuntimeError(f'Wrong NVIDIA vermagic: {module}')
    result = dict(prepared, artifacts={str(p.relative_to(ROOT)): sha(p) for p in artifacts},
                  boot_parameter=LOCK['boot_parameter'], installed=False,
                  boot_tested=False, full_kernel_built=True)
    (WORK / 'build-info.json').write_text(json.dumps(result, indent=2)+'\n')
    print('Kernel and matching NVIDIA modules built. Nothing installed.', flush=True)


def audit():
    info = json.loads((WORK / 'build-info.json').read_text())
    check(ROOT / 'source.lock.json', info['source_lock_sha256'])
    check(KERNEL / '.config', LOCK['installed_config_sha256'])
    for name, digest in info['artifacts'].items():
        check(ROOT / name, digest)
    directory = WORK / 'audit'
    directory.mkdir(exist_ok=True)
    current = Path('/usr/lib/modules') / LOCK['installed_kernel']
    installed_modules = set()
    for path in (current / 'kernel').rglob('*.ko*'):
        name = str(path.relative_to(current / 'kernel'))
        for suffix in ('.zst', '.xz', '.gz'):
            name = name.removesuffix(suffix)
        installed_modules.add(name)
    built_modules = {str(Path(name).with_suffix('.ko'))
                     for name in (KERNEL / 'modules.order').read_text().splitlines()}
    if installed_modules != built_modules:
        raise RuntimeError('The test kernel module set differs from the installed baseline')
    if any(not (KERNEL / name).is_file() for name in built_modules):
        raise RuntimeError('An expected kernel module is missing from the build')
    wifi = KERNEL / 'drivers/net/wireless/mediatek/mt76/mt7925/mt7925e.ko'
    for label, module in [('current-wifi', current / 'updates/mt7925e.ko'),
                          ('test-wifi', wifi)]:
        run(['objcopy', '--dump-section', f'.text={directory / (label + ".text")}',
             module, '/dev/null'])
    if (directory / 'current-wifi.text').read_bytes() != (directory / 'test-wifi.text').read_bytes():
        raise RuntimeError('Test Wi-Fi machine code differs from the current patched driver')
    wifi_release = subprocess.check_output(['modinfo', '-F', 'vermagic', wifi], text=True).split()[0]
    if wifi_release != LOCK['test_kernel']:
        raise RuntimeError('Wrong test Wi-Fi vermagic')
    # This patch changes no public structures or exports. Check that against
    # the actual complete symbol-version tables produced by both builds.
    def versions(path):
        return {line.split()[1]: line.split()[0] for line in path.read_text().splitlines()}
    if versions(current / 'build/Module.symvers') != versions(KERNEL / 'Module.symvers'):
        raise RuntimeError('Exported kernel symbol versions differ from the installed baseline')
    nvidia_version = subprocess.check_output(
        ['modinfo', '-F', 'version', NVIDIA / 'kernel-open/nvidia.ko'], text=True).strip()
    if nvidia_version != LOCK['nvidia_version']:
        raise RuntimeError('Unexpected NVIDIA driver version')
    result = dict(wifi_text_matches_current_patch=True,
                  matching_kernel_module_count=len(built_modules),
                  wifi_vermagic=wifi_release, wifi_module_sha256=sha(wifi),
                  nvidia_version=nvidia_version,
                  exported_symbol_crcs_match_installed=True,
                  config_matches_installed=True, runtime_tested=False)
    (directory / 'validation.json').write_text(json.dumps(result, indent=2)+'\n')
    print('Wi-Fi machine code and full kernel symbol versions match the installed baseline.', flush=True)
    return info


def package(jobs):
    info = audit()
    destination = ROOT / 'release'
    destination.mkdir(exist_ok=True)
    packaging = WORK / 'packaging'
    packaging.mkdir(exist_ok=True)
    shutil.copy2(ROOT / 'PKGBUILD', packaging / 'PKGBUILD')
    env = environment()
    env.update(N1X_SLEEP_ROOT=str(ROOT), N1X_SLEEP_JOBS=str(jobs), PKGDEST=str(destination),
               SRCDEST=str(packaging), BUILDDIR=str(packaging))
    print(f'Packaging locally; output: {WORK / "package.log"}', flush=True)
    with (WORK / 'package.log').open('a') as log:
        run(['makepkg', '--nodeps', '--noconfirm'], cwd=packaging, env=env,
            stdout=log, stderr=subprocess.STDOUT)
    finish_package(info)


def finish_package(info):
    destination = ROOT / 'release'
    packages = [p for p in destination.glob('*.pkg.tar.*')
                if p.suffix in ('.zst', '.xz', '.gz', '.bz2')]
    if len(packages) != 1:
        raise RuntimeError('Expected exactly one package in release/')
    artifact = packages[0]
    metadata = subprocess.check_output(['bsdtar', '-xOf', artifact, '.BUILDINFO'], text=True)
    if f'pkgbuild_sha256sum = {sha(ROOT / "PKGBUILD")}' not in metadata.splitlines():
        raise RuntimeError('Package was built from a different PKGBUILD')
    paths = subprocess.check_output(['bsdtar', '-tf', artifact], text=True).splitlines()
    allowed = ('usr/lib/modules/' + LOCK['test_kernel'] + '/',
               'usr/share/licenses/' + LOCK['test_pkgbase'] + '/')
    parents = {'usr/', 'usr/lib/', 'usr/lib/modules/', 'usr/share/',
               'usr/share/licenses/', '.PKGINFO', '.BUILDINFO', '.MTREE'}
    if any(p not in parents and not p.startswith(allowed) for p in paths):
        raise RuntimeError('Package contains a path outside its isolated destinations')
    info.update(package=artifact.name, package_sha256=sha(artifact),
                package_bytes=artifact.stat().st_size, installed=False)
    (destination / 'build-info.json').write_text(json.dumps(info, indent=2)+'\n')
    print(f'Packaged {artifact}. Nothing installed.', flush=True)


def audit_image(staging, output):
    listing = subprocess.check_output(['lsinitcpio', '-l', output], text=True)
    (WORK / 'initramfs-files.txt').write_text(listing)
    paths = listing.splitlines()
    names = {Path(p).name for p in paths}
    versions = {p.split('lib/modules/', 1)[1].split('/', 1)[0]
                for p in paths if 'lib/modules/' in p}
    versions.discard('')
    if versions != {LOCK['test_kernel']} or 'usr/bin/cryptsetup' not in paths:
        raise RuntimeError('Initramfs kernel version or encryption utility check failed')
    required = {}
    for name in ('nvidia', 'nvidia_modeset', 'nvidia_uvm', 'nvidia_drm',
                 'thunderbolt', 'dm_crypt', 'btrfs', 'nvme', 'i2c_mt65xx', 'i2c_hid_acpi'):
        filename = subprocess.check_output([
            'modinfo', '-b', staging / 'usr', '-k', LOCK['test_kernel'],
            '-F', 'filename', name], text=True).strip()
        if filename != '(builtin)' and Path(filename).name not in names:
            raise RuntimeError(f'Missing boot module in initramfs: {name}')
        required[name] = filename
    firmware = subprocess.check_output([
        'modinfo', '-F', 'firmware', NVIDIA / 'kernel-open/nvidia.ko'], text=True).splitlines()
    for name in firmware:
        if not any('usr/lib/firmware/' + name + suffix in paths for suffix in ('', '.zst', '.xz')):
            raise RuntimeError(f'Missing NVIDIA firmware in initramfs: {name}')
    (WORK / 'initramfs-validation.json').write_text(json.dumps({
        'kernel': LOCK['test_kernel'], 'required_modules': required,
        'nvidia_firmware': firmware, 'cryptsetup': True, 'boot_tested': False}, indent=2)+'\n')
    print('Initramfs includes the required storage, encryption, keyboard and NVIDIA support.', flush=True)


def image():
    """Exercise the current initramfs hooks against local modules, without post hooks."""
    destination = ROOT / 'release'
    info = json.loads((destination / 'build-info.json').read_text())
    check(destination / info['package'], info['package_sha256'])
    staging = WORK / 'packaging/pkg' / LOCK['test_pkgbase']
    modules = staging / 'usr/lib/modules' / LOCK['test_kernel']
    check(modules / 'vmlinuz', info['artifacts']['build/linux-7.3/arch/arm64/boot/Image'])
    run(['depmod', '-b', staging / 'usr', '-a', LOCK['test_kernel']])
    # Some installed drop-ins call modinfo without honoring --moduleroot.
    # Reproduce their configuration in the normal order, redirecting only
    # those configuration-time queries to our uninstalled module tree.
    config_files = [Path('/etc/mkinitcpio.conf')]
    dropins = [p for p in Path('/etc/mkinitcpio.conf.d').glob('*.conf') if p.is_file()]
    ordered = subprocess.check_output(
        ['sort', '-zVu'], input=b'\0'.join(str(p).encode() for p in dropins) + b'\0',
        env=dict(os.environ, LC_ALL='C.UTF-8')).split(b'\0')
    config_files += [Path(p.decode()) for p in ordered if p]
    config = WORK / 'mkinitcpio.conf'
    config.write_text('modinfo() { /usr/bin/modinfo -b "$N1X_SLEEP_MODULE_ROOT" "$@"; }\n' +
                      '\n'.join(p.read_text() for p in config_files) + '\nunset -f modinfo\n')
    (WORK / 'initramfs-config-inputs.json').write_text(json.dumps(
        {str(p): sha(p) for p in config_files}, indent=2)+'\n')
    output = destination / 'initramfs-p16-sleep.img'
    print(f'Generating local initramfs; output: {WORK / "initramfs.log"}', flush=True)
    with (WORK / 'initramfs.log').open('w') as log:
        run(['fakeroot', 'mkinitcpio', '--nopost', '-r', staging / 'usr',
             '-c', config, '-k', LOCK['test_kernel'], '-g', output],
            env=dict(os.environ, N1X_SLEEP_MODULE_ROOT=str(staging / 'usr')),
            stdout=log, stderr=subprocess.STDOUT)
    audit_image(staging, output)
    image_bytes = output.stat().st_size
    kernel_bytes = (modules / 'vmlinuz').stat().st_size
    info.update(initramfs_sha256=sha(output), initramfs_bytes=image_bytes,
                kernel_image_bytes=kernel_bytes,
                normal_boot_files_bytes=image_bytes + kernel_bytes,
                boot_available_bytes=shutil.disk_usage('/boot').free,
                installed=False, boot_tested=False)
    (destination / 'build-info.json').write_text(json.dumps(info, indent=2)+'\n')
    print(f'Local kernel + initramfs: {kernel_bytes + image_bytes:,} bytes; '
          f'/boot free: {info["boot_available_bytes"]:,} bytes. Nothing installed.', flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['tools', 'prepare', 'build', 'audit', 'package', 'image'])
    parser.add_argument('--jobs', type=int, default=6)
    args = parser.parse_args()
    if args.jobs < 1:
        parser.error('--jobs must be positive')
    if args.command == 'tools':
        tools()
    elif args.command == 'prepare':
        prepare()
    elif args.command == 'build':
        build(args.jobs)
    elif args.command == 'audit':
        audit()
    elif args.command == 'package':
        package(args.jobs)
    else:
        image()


if __name__ == '__main__':
    try:
        main()
    except (RuntimeError, OSError, subprocess.CalledProcessError) as exc:
        sys.exit(str(exc))
