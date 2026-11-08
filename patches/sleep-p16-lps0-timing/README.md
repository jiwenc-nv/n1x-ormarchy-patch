# P16 LPS0 timing experiment

An opt-in, one-driver experiment for the intermittent immediate wake on the
ASUS ProArt P16 H7607BA, BIOS `H7607BA.304.L38`. This is a **whole-kernel build**:
`power_wrap_drv` is built in, so replacing a `.ko` cannot change its callbacks.
Three enabled AC-powered lid tests completed continuous sleeps, including an
18-minute-52-second run, after two off-mode baselines repeatedly woke within
1.6–3 seconds. This supports the timing fix for the observed premature wakes;
subsequent 30-minute and two-hour battery tests also resumed successfully.
The two-hour run measured about 1.45 W across the capture. An unplugged overnight
attempt on November 7–8 subsequently failed to return to the session and ended
in a fresh boot. The cause remains unresolved; overnight reliability is not
established. See the [incident evidence](results/overnight-failure-2026-11-08.json).

Use [TESTING.md](TESTING.md) for the installation commands, reboot checkpoints,
off/on lid tests and recovery steps. The helper scripts only change system
state when you explicitly run the documented installation/rebuild/default commands;
the capture script uses a temporary trace instance during each manual test.
Start with [REPRODUCING.md](REPRODUCING.md) when building from a fresh checkout.
[TESTING.md, phase F](TESTING.md#f-measure-battery-drain-with-the-lid-closed)
documents battery measurements. After the overnight failure, prioritize
supervised failure diagnosis before extending battery endurance tests.

## Exact baseline

- Original kernel: `7.3.0-26-omarchy`, package `linux-omarchy 7.3-26`.
- [Pinned Omarchy recipe](https://github.com/omacom/omarchy-pkgs/tree/49250dc672819eb8b49727defa03de8b8f1c3e74/pkgbuilds/linux-omarchy-aarch64).
- The cached installed package's trusted Omarchy signature was verified. Its
  `.BUILDINFO` PKGBUILD SHA-256 matches that recipe exactly. Recipe source
  SHA-256 and BLAKE2 checksums passed; all 448 kernel patches were applied in
  recipe order with `--fuzz=0`.
- The recipe configuration is byte-identical to the installed headers'
  `.config`. GCC and GNU ld match the original build's versions.
- `source.lock.json` pins source, configuration, patches, and local build tools.
  `provenance/` retains package metadata, source checksums, and patch order.

This reproduces the published source/configuration, not a byte-identical
binary. The test release has a different name and generates its own module
signing key; the vendor's private key is unavailable.

## Change under test

`fix.patch` changes only
`drivers/soc/mediatek/power_wrap/power_wrap_lps0.c`:

1. With the option enabled, issue screen-off, Microsoft entry, and LPS0 entry
   notifications in `suspend_late`, before device IRQ suspension.
2. Wait 3000 ms there to allow a possible delayed firmware event to run.
3. Restore firmware state in `resume_early`, with cleanup on failed entry and
   retries for failed exit notifications.

The timing idea and lifecycle tests are adapted from
[Omarchy PR #1958 at commit 8abf19a4](https://github.com/omacom/omarchy-pkgs/tree/8abf19a4e9a96dc86d1827593d9e02d34db3db88/pkgbuilds/linux-omarchy-aarch64).
The CPU coordination changes from that PR are excluded. There is no 20-CPU
requirement and no change to which devices can wake the machine.

Enable with the boot argument:

```text
power_wrap_drv.p16_lps0_late=1
```

Without it, the driver retains its original noirq behavior. The new path also
requires exact ASUS/model/BIOS matches, MT8901, s2idle, and firmware function
mask `0x3f8`. An enabled cycle adds three seconds before sleep. A future BIOS
version requires a fresh review of the guard and firmware behavior.

The current build still needs this argument: its read-only boolean parameter
defaults to false. Keep it in the test entry's persistent configuration and
UKI; it does not need to be entered at each boot. Making the behavior default-on
in the driver would require another kernel build and validation. Retaining the
switch allows off-mode comparisons without rebuilding the kernel.

The full build also carries forward `../wifi-mt7925-reset/fix.patch`, already
active on this laptop. It is applied inside the mt76 source directory. NVIDIA
615.78.08 is reconstructed from its pinned archive and the seven distribution
patches. All 4,004 files must match `provenance/nvidia-source-checksums.json`,
the source manifest saved before the original tested build. The original
build copied installed DKMS sources; new builds no longer depend on `/usr/src`.

## Local build

Run as the normal user, from this directory:

```bash
python build.py doctor
python build.py tools
python build.py prepare
python build.py build --jobs 6
python build.py package
python build.py image
```

`tools` downloads pinned, signed Arch ARM packages and extracts them locally;
it does not install packages. `prepare` checks the pinned inputs, extracts a
fresh source tree, applies the vendor and local patches, runs `olddefconfig`,
and refuses a configuration change. It reconstructs and checks NVIDIA sources
from the pinned inputs. If interrupted, rename `build/` before preparing
again; `build` can resume a prepared compilation.

The test release is `7.3.0-26-omarchy-p16-sleep1`, intended for a separate
`linux-omarchy-p16-sleep` package and boot entry. Build output is under
`build/linux-7.3/`; matching NVIDIA modules are under `build/nvidia/kernel-open/`.
`build/build-info.json` is written only after both builds and artifact checks
succeed. The build uses ACPI, as this laptop does; it does not build the
recipe's unrelated Snapdragon device trees or auxiliary bpftool binary.

`package` writes an uninstalled Arch package to `release/`. Its files are
restricted to the new kernel's module directory and its license directory.
It includes the raw ARM64 Image and the matching, signed NVIDIA modules.
It neither replaces nor conflicts with `linux-omarchy`; it has no headers
subpackage. Keep the complete local build tree for future module builds.
Installing the package later will invoke the machine's normal kernel hooks,
so boot-image capacity and entry selection must be checked before installation.

`package` compares the Wi-Fi driver's machine code, the kernel module set and
all exported symbol versions against checksummed references archived from
the original installed kernel and Wi-Fi override. The old kernel and headers
need not remain installed to run these checks. `python build.py audit` reruns them.

`image` generates an initramfs under `release/` using the machine's current
mkinitcpio configuration and locally staged modules. Post hooks are disabled;
it does not copy anything into `/boot`. It records the combined Image/initramfs
size and the available boot space. This is a build check, not a boot test.
Configuration-time `modinfo` queries are directed to the staged modules so
conditional drop-ins, including Thunderbolt, behave as they would after
installation. The effective configuration and its input checksums are saved
under `build/`.

The unchanged baseline, original downloads, and earlier validation evidence
are in `.cache/baseline/`. Downloaded tools, source trees, binaries and logs
are ignored by git. They were moved here from `~/sleep-kernel-7.3-26/`.

This standalone builder is deliberately outside `manage.sh`'s module backend.
There is no `patch.json` registration: `manage.sh apply` continues to manage
the Wi-Fi module only. The sleep builder never writes `/boot`, `/etc`, or
`/usr/lib/modules`, and never reboots.
`target.json` still targets the stock kernel. While booted into the test kernel,
the manager can report `other-kernel`; the test kernel carries the Wi-Fi fix in
its own `mt7925e` module and does not need that stock-kernel override applied.

## Validation and next test

```bash
python tests/test_lps0_timing.py build/linux-7.3
```

The harness compiles the real callback bodies with firmware mocks, then runs
plain and AddressSanitizer/UndefinedBehaviorSanitizer builds. It covers
default-off behavior, platform guards, repeated cycles, entry/exit failures,
abort cleanup and retries. LeakSanitizer requires an environment where ptrace
is not blocked. These checks do not emulate the EC or prove the timing fix.

The 2026-11-07 build and packaging completed successfully:

- Full kernel and five NVIDIA modules built; all 8,506 distribution kernel
  modules are present. Configuration and all 32,943 exported symbol versions
  match the installed baseline. Wi-Fi executable code matches the currently
  installed patched module byte-for-byte.
- `release/linux-omarchy-p16-sleep-7.3-26.1-aarch64.pkg.tar.xz` is the separate
  package (about 204 MiB). Its payload is confined to the new kernel's module
  directory and license directory. Its SHA-256 is recorded in
  `release/build-info.json`.
- `release/initramfs-p16-sleep.img` built successfully. Its contents include
  the required NVIDIA modules/firmware, encryption, NVMe, internal keyboard
  and Thunderbolt support. Btrfs is built into the kernel.
- Kernel Image plus normal initramfs: **180.9 MiB**. Available `/boot` space
  at validation: **473.7 MiB**. This measurement covers one normal boot image
  pair, not an additional fallback pair or future snapshots.
- Callback/sanitizer tests and the repository's 20 manager tests passed.
  Detailed local evidence is in `build/audit/validation.json`,
  `build/initramfs-validation.json`, and the build logs.

The full build emitted warnings in unchanged distribution code. The initramfs
build also reported the unsupported ARM64 microcode hook and possibly missing
Renesas xHCI firmware. It completed successfully; the required-files check
above passed.

## Runtime results, 2026-11-07

The separate test package is installed and was tested with the parameter off
and on. Both original/recovery boot images retain their saved hashes. The test
entry was initially selected with `bootctl set-oneshot`. After the successful
enabled sequence, it was made the persistent default at the user's request:
`default_entry: Omarchy/linux-omarchy-p16-sleep` in `/boot/limine.conf`.
The running parameter is `Y`, and the test UKI and its generator drop-in retain
`power_wrap_drv.p16_lps0_late=1`. All runs below used AC power and the same
device setup. A subsequent normal reboot confirmed that the test kernel and
enabled parameter persist.

| Capture | Mode | Sleep periods (seconds) | Recorded suspend failures |
| --- | --- | --- | --- |
| off-01 | Off | 2.528, 2.968, 2.620, 2.870 | 0 |
| off-02 | Off | 1.969, 1.559, 1.990, 2.056 | 0 |
| on-01 | On | 121.395 | 0 |
| on-02 | On | 118.451 | 0 |
| on-long | On | 1131.942 | 0 |

Each enabled run had one continuous `machine_suspend` period ending at lid
reopening. The long run exceeded its planned 900 seconds: the journal records
1137.317 seconds with the lid closed. Traces had no dropped/overrun events.
NVIDIA 615.78.08 and network connectivity were healthy after the long test.
The approximately three-second `dpm_suspend_late` phase is consistent with
the experimental settle delay. These measurements do not prove hardware
low-power residency or reduced battery drain.

The first `off-03` capture stopped before the lid test and is invalid; the
collector now requires `done` so an extra pasted newline cannot stop it.
`off-03-retry` encountered a graphics kernel NULL dereference and required a
forced reboot. Its investigation is deferred by user request. No such oops
was recorded in the three enabled runs. A pre-existing SDHCI/PCIe hotplug
`already-free IRQ` warning recurred on the long-test resume; it also appeared
on the original kernel and is not fixed by this timing patch.

This completes the planned AC test sequence. Those three enabled AC tests share
one boot. An earlier original-kernel trace also slept for about 108
seconds, so these results support the candidate without conclusively proving
causality. Detailed local captures remain under the git-ignored `validation/`
directory. The compact comparison and original build evidence are archived
under [results/](results/). The original kernel remains available as the
recovery entry.

The subsequent battery test, after another boot, recorded one 1817.864748-second
sleep with one successful suspend and no recorded suspend failures. Both
energy endpoints reported 98.891 Wh, so battery drain is **unresolved**, not
zero. Awake follow-up readings decreased in 0.016–0.032 Wh steps, showing the
gauge updates while awake; this does not prove it accounts correctly for sleep
energy. See [the first battery result](results/battery-30m-2026-11-07.json).

The [two-hour battery test](results/battery-2h-2026-11-07.json) then completed
one 7357.740068-second sleep (2 h 2 min 38 s), ending at lid reopening, with
no recorded suspend failures or trace losses. Energy fell from 97.126 to
94.141 Wh: 2.985 Wh across a 7390.481664-second capture, or approximately
1.454 W and 1.455% of full capacity per hour. At that constant rate, a full
99.902 Wh battery would last about 68.7 hours; eight hours would use about
11.6%. Those are extrapolations, not measured endurance. The capture included
32.7 seconds outside suspend. The final sample was only 17.9 seconds after
lid opening, so the requested 60-second settling wait was not observed.
The known SDHCI/PCIe IRQ warning recurred; GPU and network checks passed.
Longer battery operation and long-term reliability remain unverified.

## Overnight failure, 2026-11-08

The next overnight attempt did not return to the session. The user reports
that AC and all peripherals were disconnected, lid/keyboard/power-button wake
attempts did not respond, and connecting AC was followed by a fresh boot.
Whether a long power-button press forced shutdown during recovery is unknown.
The failed boot and the recovery boot both used the test kernel with the
timing option enabled.

The saved journals establish this timeline (America/Los_Angeles, PST):

| Time | Evidence |
| --- | --- |
| Nov 7, 22:13:57 | The lock helper timed out waiting for a secure lock and issued the screen-lock warning. This suspend subsequently resumed at 22:14:45. |
| 22:22:38 | A `systemctl` client requested suspend. The screen locked securely, and the system resumed at 22:23:39. |
| 22:23:59–22:24:00 | Final lid close; the shell reported `secure=true` before systemd began suspend. The journal ends at `Performing sleep operation 'suspend'...`. |
| Nov 8, 08:03 | A new boot ID appears; journald reports unclean journal files. No successful overnight resume or orderly shutdown is recorded. |

The earlier lock timeout is a real separate problem, but it does not establish
the cause of the final failure: locking succeeded on that attempt. Missing
final kernel messages do not prove the hang occurred before kernel suspend
entry; messages can be lost during an unclean shutdown. No panic/oops is saved
for this incident. Both pstore directories are empty, and EFI pstore is disabled
by the running configuration, so the absence of a crash report is inconclusive.

NVIDIA logged power-source error `0x11` (`NV_ERR_GPU_NOT_FULL_POWER`) during two
resumes that completed, including the one before the final attempt. The known
SDHCI/PCIe already-free IRQ warning also recurred on successful resumes. These
are investigation leads, not proven causes. The earlier graphics NULL
dereference was not found in this boot's saved journal.

UPower's last saved charge reading was 100% at 21:49:04; the first morning
reading was 91% at 08:03:33, already charging. This contradicts an inference of
zero loss, but the samples do not bracket the final suspend. Neither actual
overnight sleep duration nor isolated sleep power can be calculated. The
previous 68.7-hour extrapolation remains a consumption estimate from one
successful test, not evidence that the laptop can reliably sleep that long.

The [sanitized incident record](results/overnight-failure-2026-11-08.json)
archives selected events, evidence hashes, limitations, and the next diagnostic
steps. Raw logs remain in ignored `validation/overnight-20261108/`. No kernel,
boot, power-management, or lock configuration was changed during this review.

`set-default.py test` makes this boot selection reproducible, and
`set-default.py stock` selects the original kernel as the persistent default.
The helper checks the installed images before and after the change, backs up
the current configuration under `validation/`, and preserves menu entries and
unrelated settings. It does not rebuild images or reboot. The checker now
accepts explicit `--default original|test|stock` policies; use
`sudo python check-boot.py --mode on --default test` for the current setup.
Configuration checks can be tested without root with
`python tests/test_boot_default.py`.

## Backpack trip, 2026-11-08

The user reported heat after carrying the laptop with the lid closed from
08:46:35 to 09:38:48 PST (52 minutes 13 seconds). With the same kernel and
timing option enabled, four s2idle operations completed during that interval.
Journal clock differences support approximately 50 minutes 30 seconds of
software timekeeping suspension, but do not establish hardware low-power
residency. The machine resumed at 08:57, 09:33, and 09:37 with no intervening
lid-open event, then suspended again about 25 seconds after each resume.

The last saved pre-trip battery reading was 99% at 08:28; it reported 92% at
09:37. These sparse percentage samples cannot establish isolated sleep power.
The NVIDIA power-source error recurred during the 08:57 resume; its role is
unproven. The last wake IRQ belongs to the final lid-open resume and cannot
identify the earlier wake causes. This trip recovered without a reboot, unlike
the overnight incident. See the [trip evidence](results/backpack-sleep-2026-11-08.json).
Next diagnostics should capture each wake cause and actual energy/residency;
use shutdown for backpack transport while heat and sleep reliability remain
unresolved.

The subsequent [instrumented backpack capture](results/backpack-power-2026-11-08.json)
recorded one 884.685793-second software suspend ending at lid reopening, with
no trace losses. Reported energy fell by 2.968 Wh across a 910.866-second
capture, approximately 11.73 W including 26.18 seconds outside suspend.
Thermal endpoints rose from 35/38 to 48/53 degrees C. The trace contains
1,755 timekeeping freeze entries inside that suspend period, about 0.508
seconds apart; the earlier low-drain two-hour trace had only two entries.
This establishes a different internal idle pattern despite one continuous
software suspend. It does not identify the interrupt or prove that this
periodic activity alone accounts for the power loss. Neither wake-source
activations nor the filtered ACPI IRQs appear inside the period; the next
diagnostic should cover other IRQs, timers, and IPIs. The requested resume
wait was six seconds and the actual endpoint was about 13.7 seconds after
lid opening, so battery-gauge settling is still a limitation.

The [tabletop follow-up](results/tabletop-power-2026-11-08.json) reproduced
the high consumption in the same boot: one 1262.498284-second suspend, 4.236 Wh
reported loss, and 11.6898 W average across the capture. Starting temperatures
were again 35/38 degrees C; endpoints were 44/48 degrees C. There were 2,493
timekeeping freeze entries with a median interval of 0.5076075 seconds and
no trace losses. The backpack is therefore not required for this behavior.
The sample was about 25.8 seconds after lid opening, so the requested 60-second
gauge-settling interval was still not observed. The periodic interrupt source
remains unidentified; next capture should cover IRQs, timers, and IPIs within
s2idle rather than only the ACPI event IRQs.

After a normal reboot, the [first fresh-boot capture](results/freshboot-recovery-2026-11-08.json)
recorded a 190.045777-second continuous suspend with only two timekeeping
freeze exits, no trace losses, and one deepest-state s2idle entry per sampled
CPU. Reported energy loss was 0.176 Wh over 215.448835 seconds (2.9408 W
including 25.40 seconds outside suspend). Thermal endpoints fell from 38/41
to 33/37 degrees C. The previous half-second idle pattern disappeared.
The kernel command line, sleep flag, mode and recorded module source versions
match the high-power boot; WirePlumber remains 0.5.19 and the power profile
remains balanced. Chrome/Slack were not running at the post-test check.
Steam had been removed before the prior boot's last diagnostic, which still
showed the half-second pattern. Driver/hardware initialization, app/session
state and uninstall effects taking effect on reboot are not yet isolated.
This first-cycle recovery does not establish repeated-cycle reliability or
restore the precision of the earlier two-hour energy measurement.
