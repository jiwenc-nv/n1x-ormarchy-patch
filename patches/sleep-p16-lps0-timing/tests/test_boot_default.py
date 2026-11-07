"""Check boot selection changes without touching the real boot partition."""
from pathlib import Path
import runpy
import unittest

ROOT = Path(__file__).resolve().parents[1]
CHECK = runpy.run_path(str(ROOT / 'check-boot.py'))['check_selection']
REPLACE = runpy.run_path(str(ROOT / 'set-default.py'))['replace_default']
TEST = 'Omarchy/linux-omarchy-p16-sleep'
STOCK = 'Omarchy/linux-omarchy'
CONFIG = '''timeout: 1
quiet: yes
default_entry: 2
/+Omarchy
  //linux-omarchy
  protocol: efi
  //linux-omarchy-fallback
  protocol: efi
  //linux-omarchy-p16-sleep
  protocol: efi
  //Snapshots
  ///old
  ////linux-omarchy
  protocol: efi
/EFI fallback
protocol: efi
'''


class BootDefaultTests(unittest.TestCase):
    def test_promotion_preserves_menu_and_unrelated_settings(self):
        result = REPLACE(CONFIG, TEST)
        self.assertEqual(result, CONFIG.replace('default_entry: 2', 'default_entry: ' + TEST))
        CHECK(result, CONFIG, 'test')
        self.assertEqual(REPLACE(result, TEST), result)
        stock = REPLACE(result, STOCK)
        CHECK(stock, CONFIG, 'stock')

    def test_remembered_entry_cannot_override_requested_default(self):
        original = 'remember_last_entry: yes\n' + CONFIG
        result = REPLACE(original, TEST)
        CHECK(result, original, 'test')
        with self.assertRaises(RuntimeError):
            CHECK(result.replace('remember_last_entry: no', 'remember_last_entry: yes'), original, 'test')

    def test_unrelated_timeout_change_is_rejected(self):
        with self.assertRaises(RuntimeError):
            CHECK(REPLACE(CONFIG, TEST).replace('timeout: 1', 'timeout: 0'), CONFIG, 'test')

    def test_snapshot_or_wrong_parent_cannot_substitute_for_target(self):
        for wrong in (CONFIG.replace('//linux-omarchy-p16-sleep', '//Snapshots\n  ///linux-omarchy-p16-sleep'),
                      CONFIG.replace('/+Omarchy', '/+Other')):
            with self.assertRaises(RuntimeError):
                CHECK(REPLACE(wrong, TEST), CONFIG, 'test')

    def test_ambiguous_default_or_target_is_rejected(self):
        with self.assertRaises(RuntimeError):
            REPLACE('default_entry: 3\n' + CONFIG, TEST)
        duplicate = CONFIG.replace('  //linux-omarchy-p16-sleep', '  //linux-omarchy-p16-sleep\n  protocol: efi\n  //linux-omarchy-p16-sleep')
        with self.assertRaises(RuntimeError):
            CHECK(REPLACE(duplicate, TEST), CONFIG, 'test')


if __name__ == '__main__':
    unittest.main()
