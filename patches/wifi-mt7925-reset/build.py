#!/usr/bin/env python3
"""Build one patched module, refusing mismatched upstream/vendor source."""
import argparse
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
SUBTREE = Path('drivers/net/wireless/mediatek/mt76')


def run(*args, cwd=None, log=None):
    args = [str(a) for a in args]
    if log:
        with log.open('a') as output:
            subprocess.run(args, cwd=cwd, stdout=output,
                           stderr=subprocess.STDOUT, check=True)
        return ''
    return subprocess.check_output(args, cwd=cwd, text=True).strip()


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    target = json.loads((ROOT.parents[1] / 'target.json').read_text())
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--kernel', default=target['kernel'],
                        help='Installed kernel release (default: shared target.json)')
    parser.add_argument('--source-ref', help='Explicit upstream tag/commit for a new kernel')
    parser.add_argument('--kernel-package', default=target['kernel_package'])
    parser.add_argument('--output-dir', type=Path, default=ROOT / 'release',
                        help='Release directory (default: ./release)')
    parser.add_argument('--jobs', type=int, default=4)
    args = parser.parse_args()
    if os.geteuid() == 0:
        parser.error('Build as your normal user, not root')
    if args.kernel != target['kernel'] or args.kernel_package != target['kernel_package']:
        parser.error('Kernel/package must match the shared target.json')
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._+-]*', args.kernel):
        parser.error('Invalid kernel release')
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._+-]*', args.kernel_package):
        parser.error('Invalid kernel package name')
    if args.jobs < 1:
        parser.error('--jobs must be positive')
    if args.source_ref and (args.source_ref.startswith('-') or
                            not re.fullmatch(r'[A-Za-z0-9_./+-]+', args.source_ref)):
        parser.error('Invalid source ref')
    headers = Path('/usr/lib/modules') / args.kernel / 'build'
    if not (headers / 'Module.symvers').is_file():
        parser.error(f'Install the matching kernel headers first: {headers}')
    if run('make', '-s', '-C', headers, 'kernelrelease') != args.kernel:
        parser.error('Headers do not match the requested kernel release')
    stock_base = headers.parent / 'kernel' / SUBTREE / 'mt7925/mt7925e.ko'
    stock = next((Path(str(stock_base) + suffix) for suffix in
                  ['', '.zst', '.xz', '.gz'] if Path(str(stock_base) + suffix).is_file()), None)
    if not stock:
        parser.error(f'Original packaged module not found: {stock_base}')

    lock = json.loads((ROOT / 'source.lock.json').read_text())
    if lock['source_commit'] != target['source_commit']:
        parser.error('Source lock must match target.json; update all patch source locks together')
    release = args.output_dir.resolve() / args.kernel
    if release.exists():
        parser.error(f'{release} already exists; retain its rollback metadata before rebuilding')
    ref = args.source_ref or lock['source_commit']
    cache = ROOT / '.cache/linux'
    cache.parent.mkdir(exist_ok=True)
    if not (cache / '.git').exists():
        run('git', 'clone', '--filter=blob:none', '--depth=1', '--sparse',
            '--branch', args.source_ref or lock['source_ref'], lock['repository'], cache)
    if run('git', 'status', '--porcelain', cwd=cache):
        raise RuntimeError(f'Cached source has local edits: {cache}')
    try:
        commit = run('git', 'rev-parse', '--verify', ref + '^{commit}', cwd=cache)
    except subprocess.CalledProcessError:
        run('git', 'fetch', '--depth=1', 'origin', ref, cwd=cache)
        commit = run('git', 'rev-parse', 'FETCH_HEAD', cwd=cache)
    run('git', 'checkout', '--detach', commit, cwd=cache)
    if commit != target['source_commit']:
        raise RuntimeError('Resolved source ref does not match target.json source_commit')
    run('git', 'sparse-checkout', 'set', SUBTREE, cwd=cache)
    makefile = (cache / 'Makefile').read_text()
    version = '.'.join(re.search(rf'^{key}\s*=\s*(\d+)', makefile, re.M)[1]
                       for key in ['VERSION', 'PATCHLEVEL', 'SUBLEVEL'])
    if not (args.kernel == version or args.kernel.startswith(version + '-')):
        raise RuntimeError(f'Source is {version}, target is {args.kernel}. '
                           'Select a matching --source-ref explicitly.')

    (ROOT / 'build').mkdir(exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix=args.kernel + '-', dir=ROOT / 'build'))
    print(f'Build evidence: {work}', flush=True)
    source = work / 'source'
    shutil.copytree(cache / SUBTREE, source)
    module_dir = source / 'mt7925'
    make = ['make', '-C', headers, f'M={module_dir}', 'CONFIG_MT7925E=m',
            'CONFIG_MT7925_COMMON=n', 'CONFIG_MT7925U=n', f'-j{args.jobs}', 'modules']

    # Prove this source reproduces the installed driver before trusting a port.
    original = work / 'stock.ko'
    if stock.suffix == '.ko':
        shutil.copy2(stock, original)
    else:
        tool = {'.zst': 'zstd', '.xz': 'xz', '.gz': 'gzip'}[stock.suffix]
        with original.open('wb') as output:
            subprocess.run([tool, '-dc', str(stock)], stdout=output, check=True)
    run(*make, log=work / 'baseline.log')
    built = module_dir / 'mt7925e.ko'
    for name, module in [('stock', original), ('baseline', built)]:
        run('objcopy', '--dump-section', f'.text={work / (name + ".text")}', module)
    if (work / 'stock.text').read_bytes() != (work / 'baseline.text').read_bytes():
        raise RuntimeError('Baseline machine code differs from the packaged driver. '
                           'Port against the exact vendor source before installing.')
    run('patch', '--batch', '--forward', '-p1', '-i', ROOT / 'fix.patch',
        cwd=source, log=work / 'patch.log')
    run(*make, log=work / 'patched.log')
    if run('modinfo', '-F', 'vermagic', built).split()[0] != args.kernel:
        raise RuntimeError('Built module kernel version mismatch')
    versions = lambda p: set(run('modprobe', '--show-modversions', p).splitlines())
    if versions(original) != versions(built):
        raise RuntimeError('Imported kernel symbol versions differ')
    info = {
        'kernel': args.kernel, 'kernel_package': args.kernel_package,
        'upstream_ref': args.source_ref or lock['source_ref'], 'upstream_commit': commit,
        'fix_commit': lock['fix_commit'], 'patch_sha256': digest(ROOT / 'fix.patch'),
        'module_sha256': digest(built),
        'module_srcversion': run('modinfo', '-F', 'srcversion', built),
        'stock_text_matches_unpatched_build': True, 'import_symbol_crcs_match': True,
        'runtime_tested': False,
    }
    release = args.output_dir.resolve() / args.kernel
    if release.exists():
        raise RuntimeError(f'{release} already exists; move it aside to retain its rollback metadata.')
    release.parent.mkdir(exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix='.stage-', dir=release.parent))
    shutil.copy2(built, stage / 'mt7925e.ko')
    (stage / 'build-info.json').write_text(json.dumps(info, indent=2) + '\n')
    stage.rename(release)
    print(f'Built and validated: {release}\nNo system modules were installed or reloaded.')


if __name__ == '__main__':
    try:
        main()
    except (RuntimeError, subprocess.CalledProcessError, OSError) as error:
        print(f'Build stopped: {error}', file=sys.stderr)
        sys.exit(1)
