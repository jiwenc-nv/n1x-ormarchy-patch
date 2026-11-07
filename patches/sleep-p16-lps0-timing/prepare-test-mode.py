#!/usr/bin/env python3
"""Write a local, reviewable Limine drop-in. Does not modify /etc or /boot."""
import argparse
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parent
KEY = 'power_wrap_drv.p16_lps0_late='


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode', choices=('off', 'on'))
    args = parser.parse_args()
    # The backend's read-only query needs no elevation; the public wrapper
    # unnecessarily elevates all commands, including this one.
    commandline = subprocess.check_output([
        '/usr/lib/limine/limine-entry-tool', '--get-cmdline', 'linux-omarchy'], text=True).strip()
    if '\n' in commandline or not all(s in commandline for s in
            ('root=/dev/mapper/root', 'cryptdevice=', 'mem_sleep_default=s2idle',
             'power_wrap_drv.usb4_release=0', 'initcall_blacklist=simpledrm_platform_driver_init')):
        raise SystemExit('The stock kernel command line differs from the expected baseline; stop and review.')
    commandline = ' '.join(p for p in commandline.split() if not p.startswith(KEY))
    commandline += ' ' + KEY + ('1' if args.mode == 'on' else '0')
    excluded = subprocess.check_output(['bash', '--noprofile', '--norc', '-c',
        'source /usr/lib/limine/limine-common-functions; load_config >/dev/null; '
        'printf "%s" "${EXCLUDE_SNAPSHOT_ENTRIES:-}"'], text=True)
    patterns = [p.strip() for p in excluded.split(',') if p.strip()]
    if 'linux-omarchy-p16-sleep' not in patterns:
        patterns.append('linux-omarchy-p16-sleep')
    output = ROOT / 'validation' / 'p16-sleep-test.conf'
    output.parent.mkdir(exist_ok=True)
    output.write_text('# Local P16 sleep experiment; applies only to the test kernel.\n'
                      'KERNEL_CMDLINE[linux-omarchy-p16-sleep]=' + commandline + '\n'
                      '# Preserve existing snapshot exclusions and exclude this experimental image.\n'
                      'EXCLUDE_SNAPSHOT_ENTRIES="' + ', '.join(patterns) + '"\n')
    print(output.read_text(), end='')
    print(f'\nSaved locally: {output}\nNo system configuration changed.')


if __name__ == '__main__':
    main()
