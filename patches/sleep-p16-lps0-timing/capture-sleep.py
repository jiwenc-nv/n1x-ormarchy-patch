#!/usr/bin/env python3
"""Capture a manually performed lid test; never initiates suspend or reboot."""
import argparse
import datetime
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import time

from battery_metrics import battery_summary, require_battery

ROOT = Path(__file__).resolve().parent
KERNEL = '7.3.0-26-omarchy-p16-sleep1'
TRACE = Path('/sys/kernel/tracing')
PARAM = Path('/sys/module/power_wrap_drv/parameters/p16_lps0_late')


def read(path):
    try:
        return Path(path).read_text().strip()
    except OSError as exc:
        return f'unavailable: {exc}'


def periods(text):
    start = None
    result = []
    for match in re.finditer(r'\s(\d+\.\d+): suspend_resume: machine_suspend\[\d+\] (begin|end)', text):
        when = float(match[1])
        if match[2] == 'begin':
            start = when
        elif start is not None:
            result.append(round(when - start, 6))
            start = None
    return result


def snapshot():
    stats = Path('/sys/power/suspend_stats')
    power_supply = {p.name: read(p / 'uevent') for p in Path('/sys/class/power_supply').iterdir()}
    power_time = time.clock_gettime(time.CLOCK_BOOTTIME)
    return {
        'time': datetime.datetime.now().astimezone().isoformat(),
        'boottime_seconds': time.clock_gettime(time.CLOCK_BOOTTIME),
        'kernel': os.uname().release,
        'boot_id': read('/proc/sys/kernel/random/boot_id'),
        'cmdline': read('/proc/cmdline'), 'patch_parameter': read(PARAM),
        'mem_sleep': read('/sys/power/mem_sleep'),
        'pm_test': read('/sys/power/pm_test'),
        'wake_irq': read('/sys/power/pm_wakeup_irq'),
        'suspend_stats': {p.name: read(p) for p in sorted(stats.iterdir()) if p.is_file()},
        'interrupts': read('/proc/interrupts'),
        'wakeup_sources': read('/sys/kernel/debug/wakeup_sources'),
        'power_supply': power_supply, 'power_supply_boottime_seconds': power_time,
        'loaded_modules': {name: read(f'/sys/module/{name}/srcversion')
                           for name in ('nvidia', 'nvidia_drm', 'mt7925e')},
    }


def interrupted(*_):
    raise KeyboardInterrupt


def wait_for_done():
    """Ignore stray newlines, including those pasted with the launch command."""
    while input('Type done and press Enter after the test to stop capturing: ').strip().lower() != 'done':
        print('Still capturing. A blank Enter does not stop the capture.', flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--mode', required=True, choices=('off', 'on'))
    parser.add_argument('--label', required=True)
    parser.add_argument('--seconds', type=int, default=120,
                        help='Planned closed-lid duration; this script does not set a wake timer')
    parser.add_argument('--battery', action='store_true',
                        help='Require external power disconnected and a discharging system battery before arming')
    parser.add_argument('--resume-wait', type=int, default=10,
                        help='Manual wait after reopening before typing done, in seconds (default: 10)')
    args = parser.parse_args()
    if not re.fullmatch(r'[a-zA-Z0-9_-]+', args.label) or args.seconds < 1 or args.resume_wait < 0:
        parser.error('Use a simple label, a positive duration, and a nonnegative resume wait')
    if os.geteuid() != 0 or not sys.stdin.isatty():
        sys.exit('Run with sudo in an interactive terminal.')
    if os.uname().release != KERNEL:
        sys.exit(f'Wrong kernel: {os.uname().release}; select {KERNEL} first.')
    expected = 'Y' if args.mode == 'on' else 'N'
    if read(PARAM) != expected:
        sys.exit(f'Expected patch parameter {expected}; found {read(PARAM)}. Nothing armed.')
    if '[s2idle]' not in read('/sys/power/mem_sleep') or '[none]' not in read('/sys/power/pm_test'):
        sys.exit('Expected s2idle and pm_test=none. Nothing armed.')
    if not (TRACE / 'instances').is_dir():
        sys.exit('tracefs is unavailable. Nothing armed; report this before testing.')
    before = snapshot()
    if args.battery:
        try:
            name, values = require_battery(before)
        except ValueError as exc:
            sys.exit(f'{exc}. Nothing armed.')
        print(f'Battery: {name}; {int(values["POWER_SUPPLY_ENERGY_NOW"]) / 1_000_000:.3f} Wh; '
              f'{values.get("POWER_SUPPLY_CAPACITY", "unknown")}% reported.', flush=True)
        if values.get('POWER_SUPPLY_CAPACITY') == '100':
            print('For a more useful drain measurement, first use the laptop unplugged until it is below 100%.', flush=True)
    base = ROOT / 'validation'
    base.mkdir(exist_ok=True)
    uid, gid = ROOT.stat().st_uid, ROOT.stat().st_gid
    os.chown(base, uid, gid)
    stamp = datetime.datetime.now().astimezone().strftime('%Y%m%d-%H%M%S-%f')
    output = base / f'{args.label}-{stamp}'
    output.mkdir(mode=0o700)
    instance = TRACE / 'instances' / f'p16_lid_{os.getpid()}_{time.time_ns()}'
    start_epoch = time.time()
    status = 'setup'
    trace_text = ''
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, interrupted)
    try:
        instance.mkdir()
        (instance / 'tracing_on').write_text('0')
        (instance / 'buffer_size_kb').write_text('1024')
        if 'boot' not in read(instance / 'trace_clock'):
            raise RuntimeError('Boot-time trace clock is unavailable; durations would be misleading')
        (instance / 'trace_clock').write_text('boot')
        (instance / 'events/power/suspend_resume/enable').write_text('1')
        (output / 'before.json').write_text(json.dumps(before, indent=2)+'\n')
        (instance / 'tracing_on').write_text('1')
        status = 'armed'
        print(f'ARMED: {output}', flush=True)
        print(f'Close the lid for {args.seconds} seconds. Reopen it, wait {args.resume_wait} seconds, then type done and press Enter here.', flush=True)
        print('Use an external timer; these waits are manual. Capture continues across every wake while the lid is closed.', flush=True)
        if args.battery:
            print(f'Keep power disconnected until results are saved. Close promptly and finish promptly after the {args.resume_wait}-second resume wait.', flush=True)
        wait_for_done()
        status = 'finished'
    except (KeyboardInterrupt, EOFError):
        status = 'cancelled'
    finally:
        try:
            if instance.exists():
                (instance / 'tracing_on').write_text('0')
                trace_text = (instance / 'trace').read_text()
                (output / 'trace.txt').write_text(trace_text)
                stats = '\n'.join(f'{p.parent.name}:\n{read(p)}' for p in sorted(instance.glob('per_cpu/cpu*/stats')))
                (output / 'trace-stats.txt').write_text(stats)
        finally:
            if instance.exists():
                (instance / 'events/enable').write_text('0')
                instance.rmdir()
        after = snapshot()
        (output / 'after.json').write_text(json.dumps(after, indent=2)+'\n')
        summary = {'status': status, 'mode': args.mode, 'planned_lid_seconds': args.seconds,
                   'capture_elapsed_seconds': round(after['boottime_seconds'] - before['boottime_seconds'], 6),
                   'machine_suspend_seconds': periods(trace_text),
                   'boot_id': before['boot_id'], 'last_wake_irq': after['wake_irq']}
        summary['battery_test_requested'] = args.battery
        summary['planned_resume_wait_seconds'] = args.resume_wait
        summary['battery'] = battery_summary(before, after, summary['machine_suspend_seconds'])
        for name in ('success', 'fail'):
            try:
                summary[name + '_delta'] = int(after['suspend_stats'][name]) - int(before['suspend_stats'][name])
            except (KeyError, ValueError):
                summary[name + '_delta'] = None
        if not summary['machine_suspend_seconds']:
            summary['warning'] = ('No completed machine_suspend period was captured. '
                                  'This is not evidence of successful sleep; last_wake_irq may be from an earlier test.')
        (output / 'summary.json').write_text(json.dumps(summary, indent=2)+'\n')
        with (output / 'journal.log').open('w') as log:
            subprocess.run(['journalctl', '-b', '--since', '@'+str(int(start_epoch)),
                            '--no-pager', '-o', 'short-iso-precise'], stdout=log, stderr=subprocess.STDOUT)
        for path in [output, *output.iterdir()]:
            os.chown(path, uid, gid)
            os.chmod(path, 0o700 if path.is_dir() else 0o600)
        print('\n'+json.dumps(summary, indent=2), flush=True)
        print(f'Saved: {output}', flush=True)


if __name__ == '__main__':
    main()
