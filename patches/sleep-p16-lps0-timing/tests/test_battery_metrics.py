"""Check units, external-power guards, and misleading gauge readings."""
import copy
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from battery_metrics import battery_summary, require_battery


def snapshot(energy, seconds, online=0):
    return {
        'boot_id': 'same-boot', 'boottime_seconds': seconds,
        'power_supply': {
            'BAT0': f'POWER_SUPPLY_TYPE=Battery\nPOWER_SUPPLY_STATUS=Discharging\nPOWER_SUPPLY_ENERGY_NOW={energy}\nPOWER_SUPPLY_ENERGY_FULL=100000000\nPOWER_SUPPLY_CAPACITY=90\n',
            'AC0': f'POWER_SUPPLY_TYPE=Mains\nPOWER_SUPPLY_ONLINE={online}\n',
            'mouse': 'POWER_SUPPLY_TYPE=Battery\nPOWER_SUPPLY_SCOPE=Device\nPOWER_SUPPLY_ONLINE=1\n',
        },
    }


class BatteryMetricsTests(unittest.TestCase):
    def test_wh_watts_percentage_and_actual_elapsed_time(self):
        # 1 Wh over 30 minutes = 2 W, 1% of this pack, 2 percentage points/hour.
        result = battery_summary(snapshot(90000000, 100), snapshot(89000000, 1900), [1780])
        self.assertTrue(result['endpoints_on_battery'])
        self.assertEqual(result['energy_used_wh'], 1)
        self.assertEqual(result['average_capture_power_w'], 2)
        self.assertEqual(result['energy_loss_percent_of_full'], 1)
        self.assertEqual(result['energy_loss_percent_of_full_per_hour'], 2)
        self.assertEqual(result['outside_machine_suspend_seconds'], 20)

    def test_ac_and_usb_c_power_are_rejected_but_mouse_is_ignored(self):
        before = snapshot(90000000, 100)
        self.assertEqual(require_battery(before)[0], 'BAT0')
        for supply in ('AC0', 'USB-C'):
            connected = copy.deepcopy(before)
            connected['power_supply'][supply] = 'POWER_SUPPLY_TYPE=USB\nPOWER_SUPPLY_SCOPE=System\nPOWER_SUPPLY_ONLINE=1\n'
            with self.assertRaises(ValueError):
                require_battery(connected)
            result = battery_summary(before, connected, [])
            self.assertNotIn('average_capture_power_w', result)
            self.assertFalse(result['endpoints_on_battery'])

    def test_unknown_supply_and_charge_units_are_not_assumed_safe(self):
        before = snapshot(90000000, 100)
        before['power_supply']['AC0'] = 'unavailable: read failed'
        with self.assertRaises(ValueError):
            require_battery(before)
        before = snapshot(90000000, 100)
        before['power_supply']['BAT0'] = before['power_supply']['BAT0'].replace('ENERGY_NOW', 'CHARGE_NOW')
        with self.assertRaises(ValueError):
            require_battery(before)

    def test_zero_and_increasing_gauge_are_not_claimed_as_no_drain(self):
        before = snapshot(90000000, 100)
        zero = battery_summary(before, snapshot(90000000, 1900), [1780])
        self.assertTrue(any('does not establish zero drain' in note for note in zero['notes']))
        increased = battery_summary(before, snapshot(91000000, 1900), [1780])
        self.assertNotIn('average_capture_power_w', increased)

    def test_missing_suspend_and_repeated_wakes_remain_visible(self):
        before, after = snapshot(90000000, 100), snapshot(89000000, 1900)
        self.assertTrue(any('not a sleep-drain result' in note for note in battery_summary(before, after, [])['notes']))
        self.assertTrue(any('Multiple suspend' in note for note in battery_summary(before, after, [2, 3])['notes']))

    def test_reboot_and_nonpositive_interval_rejected(self):
        before, after = snapshot(90000000, 100), snapshot(89000000, 1900)
        after['boot_id'] = 'new-boot'
        self.assertNotIn('average_capture_power_w', battery_summary(before, after, []))
        self.assertNotIn('average_capture_power_w', battery_summary(before, before, []))


if __name__ == '__main__':
    unittest.main()
