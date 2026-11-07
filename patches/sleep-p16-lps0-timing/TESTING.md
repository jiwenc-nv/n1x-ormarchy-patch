# P16 sleep test runbook

Start with phase A, then resume the assistant session after the first reboot.
Run the blocks in order. If a command fails, or the boot-image build reports
`ERROR`, stop and share the output before rebooting. These instructions use
the package already built locally; rebuilding the kernel is unnecessary.

## A. Install and boot with the experiment off

Keep the same charger, USB/dock and display setup for all tests.

1. Open a terminal, enter the project directory and verify the package:

   ```bash
   cd ~/n1x-ormarchy-patch/patches/sleep-p16-lps0-timing
   mkdir -p validation
   printf '%s  %s\n' '721bdbd3d881553466d947d2c3d1dcc59dc3f177a08b9fc0ef7925b67cf27f56' 'release/linux-omarchy-p16-sleep-7.3-26.1-aarch64.pkg.tar.xz' | sha256sum -c -
   uname -r
   df -h /boot
   ```

   Expect `OK`, the current `7.3.0-26-omarchy`, and roughly 474 MiB free.
   The new normal UKI needs approximately 181 MiB. Leave at least 250 MiB
   free before installation. This threshold includes room beyond the measured
   image; it does not budget an extra test fallback or snapshot image.

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

6. Save your work and reboot with a visible menu:

   ```bash
   systemctl reboot --boot-loader-menu=15s
   ```

   Expand **Omarchy**, if necessary, and select **linux-omarchy-p16-sleep**.
   If the menu timeout command is rejected, stop and share its output. If the
   test kernel cannot boot normally, restart and select **linux-omarchy**;
   **linux-omarchy-fallback** is the existing text-console rescue entry.

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
timer. Open it, wait 10 seconds for recovery, then press Enter in that terminal.
The script does not initiate suspend or set a wake timer. It keeps capturing
every cycle until you press Enter, including repeated wakes while closed.

Repeat with labels `off-02` and `off-03`. Results go into separate timestamped
directories under `validation/`. `summary.json` reports the actual
`machine_suspend` durations, success/failure count changes, and last wake IRQ;
the directory also contains snapshots, the trace and the journal.

Only the `power:suspend_resume` tracepoint is enabled in a temporary isolated
trace instance. Its boot-time clock includes time spent suspended. No wake
sources are disabled, and no function tracing is enabled. Enter or Ctrl-C
removes the instance. These traces measure suspend duration, not proof of a
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
systemctl reboot --boot-loader-menu=15s
```

Select **linux-omarchy-p16-sleep** again. After login:

```bash
uname -r
cat /sys/module/power_wrap_drv/parameters/p16_lps0_late
cat /sys/power/mem_sleep
```

Expect the same test kernel, now **Y**, and `[s2idle]`. If the parameter is
still `N`, stop; enabling the file did not change the currently running kernel.

## D. Capture enabled tests

Repeat the same physical setup and three 120-second lid tests:

```bash
cd ~/n1x-ormarchy-patch/patches/sleep-p16-lps0-timing
sudo python capture-sleep.py --mode on --label on-01 --seconds 120
```

Then use labels `on-02` and `on-03`. If these behave normally, perform a
15-minute test:

```bash
sudo python capture-sleep.py --mode on --label on-long --seconds 900
```

One continuous sleep covering most of the closed-lid interval is the expected
result. Enabled mode deliberately spends about three seconds settling before
sleep. Repeated roughly 3–5-second sleeps suggest the original issue remains.
Report any keyboard, display, Wi-Fi or resume problem alongside the saved
captures. A successful cycle alone is insufficient: the earlier stock kernel
also completed one 108-second sleep.

## Returning to the original kernel

Reboot and select **linux-omarchy**, then check `uname -r` returns
`7.3.0-26-omarchy`. This restores the original sleep driver immediately. If
Limine remembers selections, choosing the stock entry also restores that
remembered choice. Keep the test package and logs while reviewing results.

To return the test entry to off-mode instead, repeat phase C with
`prepare-test-mode.py off` and `check-boot.py --mode off`, then reboot.

The repository's root `manage.sh` still targets the stock kernel and should
not be retargeted or used to apply another Wi-Fi override to this test kernel.
