#!/usr/bin/env python3
"""Read-only checks of the installed test UKI and preserved recovery images."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import struct
import subprocess

ROOT = Path(__file__).resolve().parent
PACKAGE = 'linux-omarchy-p16-sleep'
EFI = Path('/boot/EFI/Linux')
PARAM = 'power_wrap_drv.p16_lps0_late='


def sections(data):
    pe = struct.unpack_from('<I', data, 0x3c)[0]
    if data[pe:pe+4] != b'PE\0\0':
        raise ValueError('Not a PE/UKI image')
    count = struct.unpack_from('<H', data, pe+6)[0]
    optional = struct.unpack_from('<H', data, pe+20)[0]
    result = {}
    for i in range(count):
        header = pe + 24 + optional + 40*i
        name = data[header:header+8].rstrip(b'\0').decode('ascii')
        length, _, raw_length, position = struct.unpack_from('<IIII', data, header+8)
        if length and raw_length:
            if position+raw_length > len(data):
                raise ValueError('Truncated PE section')
            result[name] = data[position:position+min(length, raw_length)]
    return result


def sha(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--mode', choices=('off', 'on'), required=True)
    args = parser.parse_args()
    if os.geteuid() != 0:
        raise SystemExit('Run with sudo; this reads the protected boot partition.')
    info = json.loads((ROOT / 'release/build-info.json').read_text())
    subprocess.run(['pacman', '-Q', PACKAGE], check=True)
    saved = (ROOT / 'validation/original-boot.sha256').read_text().splitlines()
    expected_files = {EFI / ('omarchy_' + name + '.efi')
                      for name in ('linux-omarchy', 'linux-omarchy-fallback')}
    checked = set()
    for line in saved:
        digest, name = line.split(maxsplit=1)
        path = Path(name.lstrip('*'))
        if path not in expected_files or sha(path) != digest:
            raise RuntimeError(f'Original boot image changed or unexpected path: {path}')
        checked.add(path)
    if checked != expected_files:
        raise RuntimeError('Both original boot-image hashes must be recorded before installation')
    image = EFI / ('omarchy_' + PACKAGE + '.efi')
    parts = sections(image.read_bytes())
    payload = parts['.linux']
    size = info['kernel_image_bytes']
    if hashlib.sha256(payload[:size]).hexdigest() != info['artifacts']['build/linux-7.3/arch/arm64/boot/Image'] or any(payload[size:]):
        raise RuntimeError('The UKI contains a different kernel image')
    if len(parts.get('.initrd', b'')) < 1024*1024:
        raise RuntimeError('UKI initramfs is missing or unexpectedly small')
    commandline = parts['.cmdline'].rstrip(b'\0').decode()
    arguments = shlex.split(commandline)
    stock = subprocess.check_output(['/usr/lib/limine/limine-entry-tool', '--get-cmdline',
                                     'linux-omarchy'], text=True)
    wanted = {a for a in shlex.split(stock) if not a.startswith(PARAM)}
    if not wanted.issubset(set(arguments)):
        raise RuntimeError('The test UKI lost existing boot arguments')
    switches = [a[len(PARAM):] for a in arguments if a.startswith(PARAM)]
    if (args.mode == 'on' and switches != ['1']) or (args.mode == 'off' and switches not in ([], ['0'])):
        raise RuntimeError(f'Wrong experiment mode in the UKI: {switches}')
    config = Path('/boot/limine.conf').read_text()
    original = (ROOT / 'validation/limine.before.conf').read_text()
    settings = lambda text: re.findall(r'(?im)^\s*(default_entry|remember_last_entry|timeout)\s*:\s*(.*)$', text.split('\n/', 1)[0])
    if settings(config) != settings(original):
        raise RuntimeError('Global boot selection settings changed; review before rebooting')
    titles = re.findall(r'(?m)^\s*/+\+?([^\n]+?)\s*$', config)
    for title in ('linux-omarchy', 'linux-omarchy-fallback', PACKAGE):
        if title not in titles:
            raise RuntimeError(f'Missing boot-menu entry: {title}')
    blocks = re.findall(r'(?ms)^\s*/+\+?' + re.escape(PACKAGE) +
                        r'\s*\n(.*?)(?=^\s*/|\Z)', config)
    if len(blocks) != 1:
        raise RuntimeError('Expected one unambiguous test boot entry')
    paths = re.findall(r'(?im)^\s*(?:path|kernel_path|image_path)\s*:\s*(.*)$', blocks[0])
    if len(paths) != 1 or not paths[0].split('#')[0].strip().lower().endswith('/efi/linux/' + image.name.lower()):
        raise RuntimeError('Test menu entry does not point to the verified UKI')
    for override in re.findall(r'(?im)^\s*(?:cmdline|kernel_cmdline)\s*:\s*(.*)$', blocks[0]):
        words = shlex.split(override)
        switches = [word[len(PARAM):] for word in words if word.startswith(PARAM)]
        if not wanted.issubset(set(words)) or (args.mode == 'on' and switches != ['1']) or (args.mode == 'off' and switches not in ([], ['0'])):
            raise RuntimeError('Boot-menu command-line override differs from the requested test mode')
    if titles.index('linux-omarchy') > titles.index(PACKAGE):
        raise RuntimeError('Test entry precedes the stock entry; review ordering before rebooting')
    print('Original kernel and rescue UKIs: unchanged')
    print(f'Test UKI: exact built kernel; patch {args.mode}; existing boot arguments preserved')
    print('Original boot-selection settings preserved; stock entry precedes test entry')
    if any(key.lower() == 'remember_last_entry' and value.strip().lower() == 'yes' for key, value in settings(config)):
        print('The menu remembers your selection. Select the stock kernel when finished testing.')
    print('CHECKS PASSED. You may reboot and manually select linux-omarchy-p16-sleep.')


if __name__ == '__main__':
    try:
        main()
    except (OSError, ValueError, KeyError, struct.error, RuntimeError, subprocess.CalledProcessError) as exc:
        raise SystemExit(f'STOP: {exc}\nDo not reboot yet; share this output.')
