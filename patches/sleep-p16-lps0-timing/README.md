# P16 LPS0 timing experiment

An opt-in, one-driver experiment for the intermittent immediate wake on the
ASUS ProArt P16 H7607BA, BIOS `H7607BA.304.L38`. This is a **whole-kernel build**:
`power_wrap_drv` is built in, so replacing a `.ko` cannot change its callbacks.
Three enabled AC-powered lid tests completed continuous sleeps, including an
18-minute-52-second run, after two off-mode baselines repeatedly woke within
1.6–3 seconds. This supports the timing fix for the observed premature wakes;
battery behavior and long-term reliability remain unverified.

Use [TESTING.md](TESTING.md) for the installation commands, reboot checkpoints,
off/on lid tests and recovery steps. The helper scripts only change system
state when you explicitly run the documented installation/rebuild/default commands;
the capture script uses a temporary trace instance during each manual test.
Start with [REPRODUCING.md](REPRODUCING.md) when building from a fresh checkout.
For the next unplugged runtime check, [TESTING.md, phase F](TESTING.md#f-measure-battery-drain-with-the-lid-closed)
uses `capture-sleep.py --battery` to record energy loss, elapsed time, and
suspend coverage over a 30-minute lid test, followed by a longer test if useful.

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

This completes the planned AC test sequence. All enabled tests share one boot;
battery operation, behavior across further boots, and long-term reliability
remain unverified. An earlier original-kernel trace also slept for about 108
seconds, so these results support the candidate without conclusively proving
causality. Detailed local captures remain under the git-ignored `validation/`
directory. The compact comparison and original build evidence are archived
under [results/](results/). The original kernel remains available as the
recovery entry.

`set-default.py test` makes this boot selection reproducible, and
`set-default.py stock` selects the original kernel as the persistent default.
The helper checks the installed images before and after the change, backs up
the current configuration under `validation/`, and preserves menu entries and
unrelated settings. It does not rebuild images or reboot. The checker now
accepts explicit `--default original|test|stock` policies; use
`sudo python check-boot.py --mode on --default test` for the current setup.
Configuration checks can be tested without root with
`python tests/test_boot_default.py`.
