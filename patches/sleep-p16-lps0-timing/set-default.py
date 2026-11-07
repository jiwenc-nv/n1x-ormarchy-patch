#!/usr/bin/env python3
"""Select the installed test or stock kernel by its stable Limine menu path."""
import argparse
from datetime import datetime
import fcntl
import os
from pathlib import Path
import re
import runpy
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parent
CONFIG = Path('/boot/limine.conf')


def replace_default(config, target):
    boundary = re.search(r'(?m)^[ \t]*/', config)
    if boundary is None:
        raise RuntimeError('No boot-menu entries found')
    header, entries = config[:boundary.start()], config[boundary.start():]
    header, count = re.subn(r'(?im)^([ \t]*default_entry[ \t]*:)[^\n]*',
                            lambda match: match[1] + ' ' + target, header)
    if count != 1:
        raise RuntimeError('Expected exactly one existing global default_entry')
    header = re.sub(r'(?im)^([ \t]*remember_last_entry[ \t]*:)[^\n]*',
                    lambda match: match[1] + ' no', header)
    return header + entries


def atomic_write(data):
    # Same-filesystem rename works on the FAT ESP; no ownership/mode changes.
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=CONFIG.parent, prefix='.limine-default-', delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, CONFIG)
        directory = os.open(CONFIG.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('target', choices=('test', 'stock'))
    parser.add_argument('--mode', choices=('on', 'off'), default='on',
                        help='Expected mode of the installed test UKI (default: on)')
    args = parser.parse_args()
    if os.geteuid() != 0:
        raise SystemExit('Run with sudo; this changes /boot/limine.conf.')
    checker = runpy.run_path(str(ROOT / 'check-boot.py'))
    original = (ROOT / 'validation/limine.before.conf').read_text()
    # Coordinate with the installed Limine tools, which use this same lock.
    with Path('/run/lock/boot-partition.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        before = CONFIG.read_bytes()
        current = before.decode()
        value = checker['selection_settings'](current).get('default_entry')
        policy = next((name for name, path in checker['DEFAULT_PATHS'].items() if path == value), 'original')
        command = [sys.executable, str(ROOT / 'check-boot.py'), '--mode', args.mode, '--default']
        subprocess.run(command + [policy], check=True)
        updated = replace_default(current, checker['DEFAULT_PATHS'][args.target])
        checker['check_selection'](updated, original, args.target)
        if updated == current:
            print(f'Default is already {args.target}; no change needed.')
            return
        backup = ROOT / 'validation' / datetime.now().strftime('limine.before-default-%Y%m%d-%H%M%S-%f.conf')
        with backup.open('xb') as stream:
            stream.write(before)
            stream.flush()
            os.fsync(stream.fileno())
        owner = ROOT.stat()
        os.chown(backup, owner.st_uid, owner.st_gid)
        if CONFIG.read_bytes() != before:
            raise RuntimeError('Boot configuration changed concurrently; no update made')
        try:
            atomic_write(updated.encode())
            subprocess.run(command + [args.target], check=True)
        except BaseException:
            atomic_write(before)
            raise
        print(f'Default set to {checker["DEFAULT_PATHS"][args.target]}')
        print(f'Backup: {backup}')
        print('No reboot was initiated.')


if __name__ == '__main__':
    try:
        main()
    except (OSError, ValueError, RuntimeError, subprocess.CalledProcessError) as exc:
        raise SystemExit(f'STOP: {exc}')
