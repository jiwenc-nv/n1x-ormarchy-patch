# Reproducing the tested P16 kernel

This directory contains the changes and procedure for
`7.3.0-26-omarchy-p16-sleep1`: Omarchy's `linux-omarchy 7.3-26`, the existing
MT7925 Wi-Fi fix, the opt-in P16 LPS0 timing fix, and matching NVIDIA 615.78.08
modules. It targets ASUS ProArt P16 H7607BA, BIOS `H7607BA.304.L38`, using ACPI
and s2idle. The exact platform guards are in `fix.patch`.

## What is pinned

- `source.lock.json`: kernel/recipe/NVIDIA source archives, their SHA-256
  hashes, the recipe commit, original kernel configuration, local patches,
  release name, and locally extracted build-tool packages.
- `provenance/kernel-patch-order.json` and `source-checksums.json`: the 448
  distribution patches in order and the recipe input checksums.
- `reproduction.lock.json`: the host compiler/toolchain versions, seven
  NVIDIA patches, and checksums of the additional reproduction records.
- `provenance/nvidia-source-checksums.json`: all 4,004 source files used by
  the original tested NVIDIA build. The builder reconstructs these from the
  NVIDIA archive and distribution patches; it no longer copies `/usr/src`.
  `nvidia-dkms.conf` retains the recipe-generated package metadata.
- `provenance/baseline-audit.json`: module-list, exported-symbol CRC, and
  patched Wi-Fi executable-code reference hashes. A rebuild can run the
  original comparisons without the old kernel, headers, or Wi-Fi override
  installed. The Wi-Fi reference includes `../wifi-mt7925-reset/fix.patch`.
- `provenance/build-environment.json`: build and boot-tool package versions.
  The original distribution's complete environment is also recorded in
  `provenance/package.BUILDINFO`.

The input source and configuration can be reproduced exactly. The final
package is **not promised to be byte-identical**: each fresh kernel build
generates a module-signing key, and initramfs creation uses the destination
machine's firmware, userspace, and hardware configuration. Compare source,
configuration, module set, symbol CRCs, and driver code using the builder's
checks. Historical binary hashes identify the original tested artifacts only.

## Build from a fresh checkout

Use native aarch64 Arch/Omarchy with the host toolchain versions listed in
`provenance/build-environment.json`. `build.py doctor` checks GCC, binutils,
Clang/LLVM and pahole versions and required commands. It does not install or
downgrade anything. Recreating the recorded build environment is a prerequisite
on a newer host; this repository is not an operating-system installer.

The host prerequisites are `base-devel`, `clang`, `llvm-libs`, `pahole`,
`libelf`, `openssl`, `cpio`, `gettext`, `perl`, `python`, `xxhash`, `xz`,
`zlib`, `zstd`, `libarchive`, `curl`, `kmod`, and `mkinitcpio`. Use the normal
Arch/Omarchy package manager to provision a compatible build environment.
The Omarchy/Arch signing keyring must be available to `pacman-key --verify`.
The `tools` step separately downloads, verifies and extracts the pinned
`bc`, `lld`, `rust`, `rust-bindgen`, and `rust-src` packages into `.cache/tools/`.

Allow approximately 40 GiB free disk space for a complete build and staging,
plus time for a full kernel and NVIDIA compilation. The original build used
six jobs. Do not run the build commands as root.

```bash
git clone https://github.com/jiwenc-nv/n1x-ormarchy-patch.git
cd n1x-ormarchy-patch/patches/sleep-p16-lps0-timing
python build.py doctor
python build.py tools
python build.py prepare
python build.py build --jobs 6
python build.py package --jobs 6
```

`prepare` downloads checksum-pinned inputs, applies the 448 distribution
patches with `--fuzz=0`, applies the Wi-Fi and sleep patches, reconstructs and
checks every NVIDIA source file, and runs `olddefconfig`. It refuses any
kernel configuration change. No installed NVIDIA source, previous build
directory, boot backup, or raw validation log is needed.

`build` runs the callback tests, compiles the kernel and all modules, and
checks the kernel parameter and NVIDIA module versions. `package` checks
all 8,506 module paths, all 32,943 exported symbol CRCs and the Wi-Fi driver's
machine code against the archived baseline before staging the separate
package. It does not install it. For an interrupted compilation, rerun
`build`; `prepare` deliberately refuses to overwrite an existing source tree.

`release/build-info.json` records the checksums of **your** new artifacts.
The original package's `721bdbd3...` checksum in `results/original-build.json`
is historical and should not be imposed on a newly signed rebuild.

## Boot-image prerequisites and installation

Generate and install boot images on the configured P16 Omarchy system.
The baseline setup includes encrypted Btrfs root, the stock `linux-omarchy`
and `linux-omarchy-fallback` UKIs, NVIDIA userspace/firmware compatible with
615.78.08, and Limine 12.9.6. The test package requires
`nvidia-utils=615.78.08`. Rebuilding just this historical kernel does not make
it compatible with arbitrary newer NVIDIA userspace.

`provenance/initramfs/` preserves the original initramfs configuration and
the N1x early-panel-brightness hooks. These are reference files, not an
automatic overwrite of another machine's configuration. The important inputs
are early-loaded NVIDIA, MediaTek I2C/HID and Thunderbolt modules, encryption,
NVMe and Btrfs support, plus the N1x brightness hook at the unlock prompt.
The original host used `MKINITCPIO_FALLBACK=linux-omarchy`, preserving the stock
rescue image without generating another large test fallback UKI.

On that configured system:

```bash
python build.py image
```

This stages an initramfs locally, with post hooks disabled. It checks required
modules, NVIDIA firmware, encryption support and kernel version, and reports
the image size and `/boot` free space. It uses the host's current mkinitcpio
inputs and records their hashes; no boot partition is modified.

Continue with [TESTING.md](TESTING.md) for saving **fresh** original-image
hashes/configuration, installing the test package, generating its UKI,
off/on lid testing, selecting the default, and recovery. Existing users of
the validated setup should follow the current checkpoint rather than repeat
installation. Never substitute the historical results for another machine's
own recovery backups.

The enabled configuration is:

```text
Kernel argument: power_wrap_drv.p16_lps0_late=1
Limine default:  Omarchy/linux-omarchy-p16-sleep
Sleep mode:      s2idle
```

`prepare-test-mode.py` derives the command line from the destination's stock
entry, preserving its root identifier. `set-default.py` changes the default
by menu path, checks images before and after, and retains a configuration
backup. The boot flag is still necessary because the compiled parameter
defaults to false. No other sleep-policy or graphics-driver change was made
for this experiment.

## Validation and retained evidence

```bash
python tests/test_reproduction.py
python tests/test_boot_default.py
python tests/test_capture_input.py
python tests/test_battery_metrics.py
python tests/test_lps0_timing.py build/linux-7.3
python build.py audit
```

The callback harness includes ASan/UBSan and needs an environment supporting
their runtime. For the independent repository manager tests, run
`python -m unittest discover -s tests` from the repository root.

`results/` retains the original build hashes and audit, the five valid AC lid
tests, the reboot confirmation, and the fresh-checkout reproduction check.
The two off-mode tests repeatedly woke after about 1.6–3 seconds; the three
on-mode tests each slept continuously, including 1131.942415 seconds. Battery
operation and long-term reliability remain unverified. The invalid early
capture and deferred off-mode graphics crash are described in the runbook.

Large downloaded sources, compiled artifacts, module-signing keys, raw
journals, and host-specific boot backups remain git-ignored. A fresh checkout
downloads the inputs from their pinned URLs and rejects a checksum mismatch;
it requires network access and upstream availability. For an offline archive,
retain the verified `.cache/baseline/` archives and `.cache/tools/` package/signature
pairs separately. The repository contains the changes, source identities,
checks and procedure, rather than a vendored OS/toolchain or signed binary backup.
