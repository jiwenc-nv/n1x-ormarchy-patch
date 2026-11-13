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
        'thermal_zones': {
            p.name: {'type': read(p / 'type'), 'temp_millicelsius': read(p / 'temp')}
            for p in sorted(Path('/sys/class/thermal').glob('thermal_zone*'))
        },
        'power_supply': power_supply, 'power_supply_boottime_seconds': power_time,
        'loaded_modules': {name: read(f'/sys/module/{name}/srcversion')
                           for name in ('nvidia', 'nvidia_drm', 'mt7925e')},
    }


def interrupted(*_):
    raise KeyboardInterrupt


def write_probe_command(command):
    # Python's append-mode file object seeks to EOF; tracefs rejects that seek.
    # A raw append descriptor also guarantees the shared registry is not truncated.
    fd = os.open(TRACE / 'kprobe_events', os.O_WRONLY | os.O_APPEND)
    try:
        data = (command + '\n').encode()
        if os.write(fd, data) != len(data):
            raise RuntimeError('Incomplete tracefs probe command')
    finally:
        os.close(fd)


def configure_return_probes(instance, registered):
    """Define uniquely named probes; never truncate the shared probe registry."""
    group = 'p16_ret_' + str(os.getpid()) + '_' + str(time.time_ns())
    definitions = (
        ('psci_enter', 'p', 'psci_cpu_suspend_enter state=$arg1:x32'),
        ('psci_return', 'r128', 'psci_cpu_suspend_enter state=$arg1:x32 ret=$retval:s32'),
    )
    events = []
    for name, kind, definition in definitions:
        event = f'{group}/{name}'
        write_probe_command(f'{kind}:{event} {definition}')
        registered.append(event)
        events.append(event)
    return {'events': events, 'group': group, 'maxactive': 128,
            'acpi_wrapper': 'Not probed: protected idle text; use PSCI returns and idle-state counters',
            'definitions': [f'{kind}:{group}/{name} {definition}'
                            for name, kind, definition in definitions]}


def remove_return_probes(registered):
    for event in reversed(registered):
        write_probe_command(f'-:{event}')


def idle_state_snapshot():
    return {
        cpu.name: {
            state.name: {'name': read(state / 'name'), 'desc': read(state / 'desc'),
                         's2idle_usage': read(state / 's2idle/usage')}
            for state in sorted((cpu / 'cpuidle').glob('state*'))
        }
        for cpu in sorted(Path('/sys/devices/system/cpu').glob('cpu[0-9]*'))
    }


def configure_idle_trace(instance, registered, extra_events=()):
    """Gate noisy events to machine_suspend; keep the ordinary PM trace on."""
    events = (
        'irq/irq_handler_entry', 'irq/softirq_entry',
        'timer/timer_expire_entry', 'timer/hrtimer_expire_entry',
        'timer/timer_start', 'timer/hrtimer_start',
        'ipi/ipi_entry', 'ipi/ipi_raise',
    ) + tuple(extra_events)
    result = {'scope': 'machine_suspend begin through first subsequent device PM callback',
              'enabled_events': [], 'unavailable_events': []}
    resume_trigger = instance / 'events/power/device_pm_callback_start/trigger'
    if not resume_trigger.is_file():
        raise RuntimeError('Device PM callback trigger unavailable; nothing armed')
    for event in events:
        target = instance / 'events' / event
        if not (target / 'enable').is_file():
            result['unavailable_events'].append(event)
            continue
        # A new instance starts disabled. Event triggers soft-enable the target.
        # No ACPI-only filter: record every IRQ handled during machine_suspend.
        for command, trigger, condition in [
            ('enable_event', instance / 'events/power/suspend_resume/trigger',
             ' if action == "machine_suspend" && start == 1'),
            ('disable_event', resume_trigger, ''),
        ]:
            rule = f'{command}:{event.replace("/", ":")}{condition}'
            trigger.write_text(rule)
            registered.append((trigger, rule))
        result['enabled_events'].append(event)
    if 'irq/irq_handler_entry' not in result['enabled_events']:
        raise RuntimeError('Broad IRQ trace is unavailable; nothing armed')
    return result


def remove_idle_triggers(instance, registered):
    for trigger, rule in reversed(registered):
        trigger.write_text('!' + rule)


def wait_for_done():
    """Ignore stray newlines, including those pasted with the launch command."""
    while input('Type done and press Enter after the test to stop capturing: ').strip().lower() != 'done':
        print('Still capturing. A blank Enter does not stop the capture.', flush=True)


def wait_for_lid_cycle(cursor, resume_wait):
    """Wait on journal events without scheduling periodic suspend-time polls."""
    process = subprocess.Popen(
        ['journalctl', '-b', '-u', 'systemd-logind', '--follow',
         '--after-cursor', cursor, '--no-pager', '-o', 'cat'],
        stdout=subprocess.PIPE, text=True,
    )
    closed = False
    try:
        for line in process.stdout:
            if line.strip() == 'Lid closed.':
                closed = True
            elif closed and line.strip() == 'Lid opened.':
                print(f'Lid reopened; finishing automatically after {resume_wait} seconds. Keep power disconnected.', flush=True)
                time.sleep(resume_wait)
                return
        raise RuntimeError('Lid event monitor stopped before a close/open cycle completed')
    finally:
        process.terminate()
        process.wait(timeout=5)
        process.stdout.close()


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
    parser.add_argument('--wake-details', action='store_true',
                        help='Also trace wake-source activity and ACPI event IRQs in the temporary instance')
    parser.add_argument('--idle-details', action='store_true',
                        help='Trace all handled IRQs, timer callbacks and IPIs during machine_suspend; implies --wake-details')
    parser.add_argument('--auto-finish', action='store_true',
                        help='Finish after a recorded lid close/open cycle and --resume-wait; no done input required')
    parser.add_argument('--idle-returns', action='store_true',
                        help='Add temporary PSCI entry/return probes; implies --idle-details')
    args = parser.parse_args()
    args.idle_details = args.idle_details or args.idle_returns
    args.wake_details = args.wake_details or args.idle_details
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
    idle_triggers = []
    return_probes = []
    return_trace = {'requested': args.idle_returns}
    idle_trace = {'requested': args.idle_details}
    wake_trace = {'requested': args.wake_details, 'enabled_events': [],
                  'unavailable_events': [], 'acpi_event_irqs': []}
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, interrupted)
    try:
        instance.mkdir()
        (instance / 'tracing_on').write_text('0')
        (instance / 'buffer_size_kb').write_text('8192' if args.idle_details else '1024')
        if 'boot' not in read(instance / 'trace_clock'):
            raise RuntimeError('Boot-time trace clock is unavailable; durations would be misleading')
        (instance / 'trace_clock').write_text('boot')
        (instance / 'events/power/suspend_resume/enable').write_text('1')
        if args.idle_returns:
            return_trace.update(configure_return_probes(instance, return_probes))
            (output / 'return-probes.json').write_text(json.dumps(return_trace, indent=2)+'\n')
            (output / 'cpu-idle-before.json').write_text(json.dumps(idle_state_snapshot(), indent=2)+'\n')
        if args.wake_details:
            events = ['power/wakeup_source_activate', 'power/wakeup_source_deactivate']
            # Discover this boot's IRQ assignment; never assume IRQ 72 is stable.
            irqs = sorted({int(m[1]) for m in re.finditer(
                r'^\s*(\d+):[^\n]*\bACPI:Event\b', before['interrupts'], re.MULTILINE)})
            wake_trace['acpi_event_irqs'] = irqs
            irq_event = instance / 'events/irq/irq_handler_entry'
            if args.idle_details:
                pass  # Broad IRQ tracing below replaces the ACPI-only filter.
            elif irqs and (irq_event / 'filter').is_file():
                (irq_event / 'filter').write_text(' || '.join(f'irq == {irq}' for irq in irqs))
                events.append('irq/irq_handler_entry')
            else:
                wake_trace['unavailable_events'].append('irq/irq_handler_entry (ACPI event IRQ filter unavailable)')
            for event in events:
                enable = instance / 'events' / event / 'enable'
                if enable.is_file():
                    enable.write_text('1')
                    wake_trace['enabled_events'].append(event)
                else:
                    wake_trace['unavailable_events'].append(event)
            (output / 'wake-trace.json').write_text(json.dumps(wake_trace, indent=2)+'\n')
        if args.idle_details:
            idle_trace.update(configure_idle_trace(instance, idle_triggers, return_probes))
            (output / 'idle-trace.json').write_text(json.dumps(idle_trace, indent=2)+'\n')
        (output / 'before.json').write_text(json.dumps(before, indent=2)+'\n')
        cursor = None
        if args.auto_finish:
            latest = subprocess.check_output(
                ['journalctl', '-b', '-n', '1', '--no-pager', '-o', 'json'], text=True)
            cursor = json.loads(latest)['__CURSOR']
        (instance / 'tracing_on').write_text('1')
        status = 'armed'
        print(f'ARMED: {output}', flush=True)
        if args.wake_details:
            print('Wake tracing: '+json.dumps(wake_trace), flush=True)
        if args.idle_details:
            print('Idle tracing: '+json.dumps(idle_trace), flush=True)
        if args.idle_returns:
            print('Firmware return tracing: '+json.dumps(return_trace), flush=True)
        if args.auto_finish:
            print(f'Close the lid for {args.seconds} seconds using an external timer. After reopening, capture finishes automatically after {args.resume_wait} seconds; no typing required.', flush=True)
        else:
            print(f'Close the lid for {args.seconds} seconds. Reopen it, wait {args.resume_wait} seconds, then type done and press Enter here.', flush=True)
        print('Use an external timer; these waits are manual. Capture continues across every wake while the lid is closed.', flush=True)
        if args.battery:
            print(f'Keep power disconnected until results are saved. Close promptly and finish promptly after the {args.resume_wait}-second resume wait.', flush=True)
        if args.auto_finish:
            wait_for_lid_cycle(cursor, args.resume_wait)
        else:
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
                remove_idle_triggers(instance, idle_triggers)
                (instance / 'events/enable').write_text('0')
                instance.rmdir()
            if return_probes:
                profile = read(TRACE / 'kprobe_profile')
                group = return_probes[0].split('/')[0]
                (output / 'return-probe-profile.txt').write_text(
                    '\n'.join(line for line in profile.splitlines() if group in line)+'\n')
                remove_return_probes(return_probes)
        after = snapshot()
        if args.idle_returns:
            (output / 'cpu-idle-after.json').write_text(json.dumps(idle_state_snapshot(), indent=2)+'\n')
        (output / 'after.json').write_text(json.dumps(after, indent=2)+'\n')
        summary = {'status': status, 'mode': args.mode, 'planned_lid_seconds': args.seconds,
                   'capture_elapsed_seconds': round(after['boottime_seconds'] - before['boottime_seconds'], 6),
                   'machine_suspend_seconds': periods(trace_text),
                   'boot_id': before['boot_id'], 'last_wake_irq': after['wake_irq']}
        summary['battery_test_requested'] = args.battery
        summary['planned_resume_wait_seconds'] = args.resume_wait
        summary['auto_finish_on_lid_cycle'] = args.auto_finish
        summary['wake_trace'] = wake_trace
        summary['idle_trace'] = idle_trace
        summary['return_trace'] = return_trace
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
