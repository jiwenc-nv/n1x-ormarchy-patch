# P16 sleep test runbook

**Current checkpoint, 2026-11-07:** `off-01` and `off-02` provide the baseline:
eight short sleep periods across two AC-powered 120-second lid tests. Phase C
is complete: the test kernel is running with parameter `Y`. `on-01` and
`on-02` recorded single continuous sleeps of 121.394864 and 118.451021 seconds,
ending at lid reopening. `on-long` then completed a single 1131.942415-second
sleep. All three enabled captures have zero recorded suspend failures and no
kernel oops. The planned AC sequence is complete; battery behavior and
long-term reliability remain unverified. The graphics driver has not been
changed. Results and the persistent SDHCI resume warning are summarized in
`results/sleep-comparison-2026-11-07.json` and [README.md](README.md#runtime-results-2026-11-07).

**Boot default:** Following those tests, `linux-omarchy-p16-sleep` is now the
persistent default, with `power_wrap_drv.p16_lps0_late=1` verified in its UKI
and generator configuration. The current session already reports `Y`; this
default change did not require a reboot. A subsequent normal reboot confirmed
`7.3.0-26-omarchy-p16-sleep1` and `Y`. Both stock/recovery UKIs are unchanged.
Use phase E to verify or reproduce the default setting. Phases A–D below are
the original installation and test sequence; do not restart them on this
already-configured machine.
The next runtime check is the unplugged battery-drain test in phase F.

`off-03` ended before the lid test and is invalid. `off-03-retry` encountered
a graphics kernel NULL dereference with the timing experiment disabled and
required a forced reboot. Investigation of that crash is deferred; it is not
evidence that the timing patch works or fails. Saved evidence is in
`validation/hang-20261107-1422/diagnosis.json` and the accompanying journals.

For a fresh checkout, first build/package the kernel and stage its initramfs
using [REPRODUCING.md](REPRODUCING.md). For a fresh installation of those built
artifacts, start with phase A, then resume the assistant session after the first reboot.
Run the blocks in order. If a command fails, or the boot-image build reports
`ERROR`, stop and share the output before rebooting. These instructions use
the package already built locally; rebuilding the kernel is unnecessary.

## A. Install and boot with the experiment off

Keep the same charger, USB/dock and display setup for all tests.

1. Open a terminal, enter the project directory and verify the package:

   ```bash
   cd ~/n1x-ormarchy-patch/patches/sleep-p16-lps0-timing
   mkdir -p validation
   python - <<'PY'
   import hashlib, json
   from pathlib import Path
   info = json.loads(Path('release/build-info.json').read_text())
   package = Path('release') / info['package']
   with package.open('rb') as stream:
       digest = hashlib.file_digest(stream, 'sha256').hexdigest()
   if digest != info['package_sha256']:
       raise SystemExit('STOP: package checksum differs from this build record')
   print(package, 'OK')
   print('Measured kernel + initramfs bytes:', info['normal_boot_files_bytes'])
   PY
   uname -r
   df -h /boot
   ```

   Expect `OK` and the original `7.3.0-26-omarchy` before a fresh installation.
   Compare free space with this build's measured size. The original test's
   normal UKI needed approximately 181 MiB. Leave at least 250 MiB
   free before installation. This threshold includes room beyond the measured
   image; it does not budget an extra test fallback or snapshot image.
   A rebuild has its own package hash because it generates a new signing key.
   Do not use the original historical package hash to verify a fresh build.

2. Save the original boot configuration and image hashes **once, before
   installation**. Retain these files across all later phases:

   ```bash
   sudo cat /boot/limine.conf > validation/limine.before.conf
   sudo sha256sum /boot/EFI/Linux/omarchy_linux-omarchy.efi /boot/EFI/Linux/omarchy_linux-omarchy-fallback.efi > validation/original-boot.sha256
   cat validation/original-boot.sha256
   ```

   There must be two hash lines. If either filename is absent, stop; do not
   substitute an image or continue with an empty backup.

3. Prepare the off-mode configuration, then install that one drop-in:

   ```bash
   python prepare-test-mode.py off
   sudo install -m 644 validation/p16-sleep-test.conf /etc/limine-entry-tool.d/zzzz-p16-sleep-test.conf
   ```

   The generator copies the stock entry's complete boot arguments and changes
   only the test kernel's experiment flag. It also preserves existing snapshot
   exclusions and excludes the experimental entry, avoiding extra copies on
   this 2 GiB boot partition. The stock entry's arguments remain unchanged.

4. Install the additional package. Answer `Y` at pacman's confirmation:

   ```bash
   sudo pacman -U ./release/linux-omarchy-p16-sleep-7.3-26.1-aarch64.pkg.tar.xz
   ```

   This installs the new kernel/modules and runs the normal Limine/mkinitcpio
   hooks. It should create a UKI for `linux-omarchy-p16-sleep`. It does not
   replace `linux-omarchy`, and no extra test fallback image is configured.
   Do not continue if image generation fails, even if pacman itself finishes.

5. Verify the installed boot image and recovery entries:

   ```bash
   sudo python check-boot.py --mode off
   limine-entry-tool --tree
   ```

   The checker must print **CHECKS PASSED**. It verifies the exact kernel
   payload inside the UKI, off-mode arguments, retained stock arguments,
   unchanged original/rescue image hashes, and retained boot-selection settings.
   The menu must contain `linux-omarchy`, `linux-omarchy-fallback`, and
   `linux-omarchy-p16-sleep`. The Limine command handles its own sudo prompt.

6. Select the test kernel for the next boot only:

   ```bash
   sudo bootctl set-oneshot Omarchy.linux-omarchy-p16-sleep
   ```

   If this command fails, stop and share its output. It sets Limine's
   `LoaderEntryOneShot` variable without changing the configured default.
   After it succeeds, save your work and reboot:

   ```bash
   systemctl reboot
   ```

   Limine should automatically select the test entry. Its menu can remain
   hidden because this installation uses `quiet: yes`. To reveal it manually,
   use `systemctl reboot --boot-loader-menu=30s` instead and press a key during
   the bootloader's wait. Expand **Omarchy**, if necessary, and select
   **linux-omarchy-p16-sleep**. If the test kernel cannot boot normally,
   restart and select **linux-omarchy**; **linux-omarchy-fallback** is the
   existing text-console rescue entry.

   Do not use `systemctl reboot --boot-loader-entry=Omarchy.linux-omarchy-p16-sleep`
   here. On this system, logind lists UKI filenames, while Limine publishes
   dotted menu identifiers in EFI `LoaderEntries`; systemctl rejects the
   Limine identifier. `bootctl set-oneshot` writes the identifier directly.
   This behavior was checked against [systemd v262's implementation](https://github.com/systemd/systemd/blob/v262/src/bootctl/bootctl-set-efivar.c)
   and [Limine v12.9.6's implementation](https://github.com/Limine-Bootloader/Limine/blob/v12.9.6/common/lib/bli.c).

7. After login, verify the running kernel and drivers:

   ```bash
   uname -r
   cat /sys/module/power_wrap_drv/parameters/p16_lps0_late
   cat /sys/power/mem_sleep
   nvidia-smi --query-gpu=name,driver_version --format=csv,noheader
   nmcli general status
   ```

   Expect `7.3.0-26-omarchy-p16-sleep1`, `N`, and `[s2idle]`. Confirm that the
   display, internal keyboard/touchpad and Wi-Fi work normally. **Resume the
   assistant session here** with these results before changing the mode.

## B. Capture the off-mode baseline

From the patch directory, run one command at a time:

```bash
cd ~/n1x-ormarchy-patch/patches/sleep-p16-lps0-timing
sudo python capture-sleep.py --mode off --label off-01 --seconds 120
```

Wait for `ARMED`. Close the lid completely for 120 seconds, using an external
timer. Open it, wait 10 seconds for recovery, then type `done` and press Enter
in that terminal. Both waits are manual; the script does not count them down.
The script does not initiate suspend or set a wake timer. It keeps capturing
every cycle until you enter `done`, including repeated wakes while closed.
Blank lines are ignored, so an extra newline pasted with the launch command
cannot end the capture immediately.

Repeat with label `off-02`. Results go into separate timestamped
directories under `validation/`. `summary.json` reports the actual
`machine_suspend` durations, total capture duration, success/failure count
changes, and last wake IRQ;
the directory also contains snapshots, the trace and the journal.
An empty sleep-duration list is not a successful sleep result. Check the
capture duration and journal before counting that run; the wake IRQ may be
left over from an earlier test.

Only the `power:suspend_resume` tracepoint is enabled in a temporary isolated
trace instance. Its boot-time clock includes time spent suspended. No wake
sources are disabled, and no function tracing is enabled. Entering `done` or
pressing Ctrl-C removes the instance. These traces measure suspend duration, not proof of a
particular CPU power residency.

## C. Enable the experiment and reboot

After reviewing the baseline, run:

```bash
python prepare-test-mode.py on
sudo install -m 644 validation/p16-sleep-test.conf /etc/limine-entry-tool.d/zzzz-p16-sleep-test.conf
limine-mkinitcpio linux-omarchy-p16-sleep
sudo python check-boot.py --mode on
```

The rebuild targets only the test package. This machine boots a UKI, so
changing the configuration file alone is insufficient. Wait for image
generation to finish and **CHECKS PASSED**, then:

```bash
sudo bootctl set-oneshot Omarchy.linux-omarchy-p16-sleep
```

If that succeeds, save your work and run `systemctl reboot`. After login:

```bash
uname -r
cat /sys/module/power_wrap_drv/parameters/p16_lps0_late
cat /sys/power/mem_sleep
```

Expect the same test kernel, now **Y**, and `[s2idle]`. If the parameter is
still `N`, stop; enabling the file did not change the currently running kernel.

## D. Capture enabled tests

Keep the same physical setup and start with one 120-second lid test:

```bash
cd ~/n1x-ormarchy-patch/patches/sleep-p16-lps0-timing
sudo python capture-sleep.py --mode on --label on-01 --seconds 120
```

Review that result before continuing with `on-02`, matching the two completed
baseline runs. If both behave normally, perform a 15-minute test:

```bash
sudo python capture-sleep.py --mode on --label on-long --seconds 900
```

One continuous sleep covering most of the closed-lid interval is the expected
result. Enabled mode deliberately spends about three seconds settling before
sleep. Repeated roughly 3–5-second sleeps suggest the original issue remains.
Report any keyboard, display, Wi-Fi or resume problem alongside the saved
captures. A successful cycle alone is insufficient: the earlier stock kernel
also completed one 108-second sleep.

## E. Keep the enabled test kernel as the default

This has already been applied on this machine. To verify the current setup:

```bash
cd ~/n1x-ormarchy-patch/patches/sleep-p16-lps0-timing
sudo python check-boot.py --mode on --default test
```

To reproduce the default change after completing phase C:

```bash
sudo python set-default.py test
```

This verifies the installed test UKI is enabled, saves the existing Limine
configuration, and sets `default_entry: Omarchy/linux-omarchy-p16-sleep`.
It uses a menu path supported by the installed Limine 12.9.6, so changing menu
positions does not redirect the default. It preserves the menu and timeout,
and disables `remember_last_entry` if that setting was present. It verifies
the result and restores the prior configuration if the final check fails.
No image rebuild or reboot is performed.

The enabled argument remains in
`/etc/limine-entry-tool.d/zzzz-p16-sleep-test.conf` for later test UKI rebuilds.
The compiled parameter defaults to off, so removing the argument would disable
the timing fix. Keep the switch; eliminating it would require a kernel rebuild.

The installed Limine documentation at `/usr/share/doc/limine/CONFIG.md`
describes the precedence: an explicit `default_entry` overrides the persistent
EFI `LoaderEntryDefault` variable. Therefore `bootctl set-default` alone would
not change this machine's configured default. A `bootctl set-oneshot` request
still takes precedence for a single boot.

## F. Measure battery drain with the lid closed

Start with **30 minutes**. If suspend/resume works and the drain is reasonable,
repeat for **two hours** to reduce the relative effect of gauge rounding and
the short awake periods at the start/end. There is no battery result yet.

1. Save your work. Disconnect the charger and any USB-C source that supplies
   power; keep the other peripherals and power settings unchanged. If starting
   at 100%, use the laptop unplugged until it reports 99% or below so the test
   does not depend on a possible full-charge gauge plateau. Do not plug in
   again until the capture has finished saving.
2. From this directory, run:

   ```bash
   sudo python capture-sleep.py --mode on --battery --label battery-30m-01 --seconds 1800
   ```

   `--battery` checks that the system battery reports `Discharging`, that
   usable `ENERGY_NOW`/`ENERGY_FULL` readings exist, and that AC/USB-C power
   sources report offline. It ignores peripheral batteries, including this
   laptop's HID and mouse battery devices. If the check refuses to arm, allow
   the unplugged telemetry to settle and retry.
3. After `ARMED`, close the lid promptly. Use a phone/external timer for
   30 minutes. Open the lid, wait the same 10 seconds used in the AC tests,
   then promptly type `done` and Enter. Keep the charger disconnected until
   the JSON summary and saved path appear. There is no automatic wake timer;
   a longer manual interval is fine and the calculation uses actual elapsed
   time, not the requested `--seconds` value.

The `battery` section reports energy before/after, Wh used, average capture
power in watts, energy loss as a percentage of starting full capacity and per
hour, completed suspend duration, and time outside `machine_suspend`.
Linux reports `ENERGY_*` in micro-watt-hours; the script converts those units
directly. It does not convert rounded percentages into energy or infer sleep
power from the instantaneous awake `POWER_NOW` value. See the
[kernel power-supply documentation](https://docs.kernel.org/power/power_supply_class.html).

**The energy measurement covers the entire capture**, including closing the
lid, transitions, and the 10-second recovery wait. The trace shows how much
of that interval was suspended; it does not provide separate energy counters
for those phases. Average capture power is therefore an initial estimate for
this lid-close test, not an isolated measurement of steady-state sleep power.
Only the endpoints are checked for external power: keep it disconnected
throughout. No periodic polling, wake timer, or system sleep hook is added.

One continuous sleep covering most of the 30 minutes is expected. Multiple
short periods would reproduce the premature-wake symptom. A zero energy
delta does not prove zero drain; the gauge may need a longer interval to show
the loss. If it reports increasing energy or external power at either end,
the script withholds the discharge-rate calculation. Review the saved summary
and trace before extending the duration.

For a follow-up two-hour test after reviewing the first result:

```bash
sudo python capture-sleep.py --mode on --battery --label battery-2h-01 --seconds 7200
```

Use the same unplugged setup and manual finish procedure. Battery measurement
checks can be run without arming a capture:

```bash
python tests/test_battery_metrics.py
python tests/test_capture_input.py
```

## Returning to the original kernel

For one stock-kernel boot, run:

```bash
sudo bootctl set-oneshot Omarchy.linux-omarchy
```

If it succeeds, save your work and run `systemctl reboot`. Check that
`uname -r` returns `7.3.0-26-omarchy`. The following boot will use the configured
test default again. To restore the stock kernel as the persistent default:

```bash
cd ~/n1x-ormarchy-patch/patches/sleep-p16-lps0-timing
sudo python set-default.py stock
```

The menu also retains **linux-omarchy** and the text-console rescue entry
**linux-omarchy-fallback**. Keep the test package and logs while reviewing
results.

To return the test entry to off-mode instead, repeat phase C with
`prepare-test-mode.py off` and `check-boot.py --mode off --default test`, then
reboot. Use `--default stock` instead if you have restored the stock default.
Changing the mode requires rebuilding the test UKI, not the kernel itself.

The repository's root `manage.sh` still targets the stock kernel and should
not be retargeted or used to apply another Wi-Fi override to this test kernel.
