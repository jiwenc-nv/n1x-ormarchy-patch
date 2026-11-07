"""Battery energy accounting from saved Linux power_supply uevents.

ENERGY_* is in micro-watt-hours; CAPACITY is a rounded percentage.
See https://docs.kernel.org/power/power_supply_class.html
"""


def supplies(snapshot):
    return {name: dict(line.split('=', 1) for line in text.splitlines() if '=' in line)
            for name, text in snapshot.get('power_supply', {}).items()}


def battery_state(snapshot):
    batteries, online, unknown = {}, [], []
    for name, values in supplies(snapshot).items():
        kind = values.get('POWER_SUPPLY_TYPE')
        if values.get('POWER_SUPPLY_SCOPE') == 'Device':
            continue
        if kind == 'Battery':
            if values.get('POWER_SUPPLY_PRESENT', '1') == '1':
                batteries[name] = values
        elif kind and values.get('POWER_SUPPLY_ONLINE') == '1':
            online.append(name)
        elif not kind or values.get('POWER_SUPPLY_ONLINE') != '0':
            unknown.append(name)
    return batteries, online, unknown


def require_battery(snapshot):
    batteries, online, unknown = battery_state(snapshot)
    if online:
        raise ValueError('External power is connected: ' + ', '.join(online))
    if unknown:
        raise ValueError('Cannot determine external-power state: ' + ', '.join(unknown))
    if len(batteries) != 1:
        raise ValueError('Expected exactly one system battery (peripheral batteries are excluded)')
    name, values = next(iter(batteries.items()))
    if values.get('POWER_SUPPLY_STATUS') != 'Discharging':
        raise ValueError(f'{name} must report Discharging; unplug power and wait for telemetry to settle')
    for key in ('ENERGY_NOW', 'ENERGY_FULL'):
        value = int(values.get('POWER_SUPPLY_' + key, '-1'))
        if value <= 0:
            raise ValueError(f'{name} has no usable {key} reading')
    return name, values


def battery_summary(before, after, sleep_periods):
    result = {
        'measurement_scope': 'Entire capture, including awake and suspend/resume overhead',
        'power_check_scope': 'Endpoints only; keep all external power disconnected throughout',
        'endpoints_on_battery': False,
    }
    try:
        name, first = require_battery(before)
        last_name, last = require_battery(after)
        if name != last_name or before.get('boot_id') != after.get('boot_id'):
            raise ValueError('Battery identity or boot changed during the capture')
        start = before.get('power_supply_boottime_seconds', before['boottime_seconds'])
        end = after.get('power_supply_boottime_seconds', after['boottime_seconds'])
        elapsed = end - start
        if elapsed <= 0:
            raise ValueError('Nonpositive battery measurement interval')
        result['endpoints_on_battery'] = True
        energy_before = int(first['POWER_SUPPLY_ENERGY_NOW']) / 1_000_000
        energy_after = int(last['POWER_SUPPLY_ENERGY_NOW']) / 1_000_000
        full_before = int(first['POWER_SUPPLY_ENERGY_FULL']) / 1_000_000
        full_after = int(last['POWER_SUPPLY_ENERGY_FULL']) / 1_000_000
        used = energy_before - energy_after
        suspended = sum(sleep_periods)
        result.update(
            battery=name, measurement_seconds=round(elapsed, 6),
            energy_before_wh=energy_before, energy_after_wh=energy_after,
            energy_full_before_wh=full_before, energy_full_after_wh=full_after,
            energy_used_wh=round(used, 6),
            reported_capacity_before_percent=first.get('POWER_SUPPLY_CAPACITY'),
            reported_capacity_after_percent=last.get('POWER_SUPPLY_CAPACITY'),
            machine_suspend_total_seconds=round(suspended, 6),
            outside_machine_suspend_seconds=round(max(0, elapsed - suspended), 6),
            machine_suspend_fraction_percent=round(100 * suspended / elapsed, 3),
        )
        notes = []
        if used < 0:
            notes.append('Gauge energy increased; no discharge rate calculated. Check charging or gauge recalibration.')
        else:
            result.update(
                average_capture_power_w=round(used * 3600 / elapsed, 4),
                energy_loss_percent_of_full=round(100 * used / full_before, 4),
                energy_loss_percent_of_full_per_hour=round(100 * used / full_before * 3600 / elapsed, 4),
            )
            if used == 0:
                notes.append('No reported energy change; this does not establish zero drain. A longer test may resolve it.')
        if full_before != full_after:
            notes.append('Reported full capacity changed; percentage loss uses the starting full capacity.')
        if first.get('POWER_SUPPLY_CAPACITY') == '100':
            notes.append('Started at reported 100%; a repeat below full charge helps avoid a top-of-charge gauge plateau.')
        if not sleep_periods:
            notes.append('No completed machine_suspend interval; this is not a sleep-drain result.')
        if len(sleep_periods) > 1:
            notes.append('Multiple suspend intervals occurred; inspect the trace and lid events for premature wakes.')
        result['notes'] = notes
    except (ValueError, TypeError, KeyError) as exc:
        result['unavailable_reason'] = str(exc)
    return result
