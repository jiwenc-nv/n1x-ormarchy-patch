# MT7925 reset crash fix for Omarchy

Build and manage a minimal `mt7925e` replacement for an installed Omarchy
kernel. This fixes the reset-path NULL-pointer crash that leaves networking
processes blocked and can prevent shutdown. It does **not** fix spontaneous
wakes or establish reliable suspend/resume.

## Patch provenance

- [morrownr/mt76 PR #122](https://github.com/morrownr/mt76/pull/122)
- [Commit 5a0af52f845a](https://github.com/morrownr/mt76/commit/5a0af52f845a86f885312c8a39e5705be979cab8)
- `fix.patch` is the downloaded commit patch, retained locally for review.
- `source.lock.json` pins the original Linux v7.3 source commit and records
  the patch's origin. This is a community patch, not an official Omarchy release.

MT7925 advertises a WM2 interrupt mask but never allocates the matching RX
queue. The patch checks the queue's descriptor count before calling
`napi_disable()` on it. The original crash was at
`mt7925e_mac_reset+0x3cc/0x500` on ASUS ProArt P16 H7607BA,
kernel `7.3.0-26-omarchy`.

## Build

Requirements: matching installed kernel and headers, GCC, make, git, Python 3,
patch, kmod, binutils, zstd (or the stock module's decompressor), and the
kernel build tools required by the headers, including pahole for BTF.
Source downloads need network access. Build as your normal user.

```bash
./build.py
```

By default this targets `uname -r` and uses the pinned source. For a newly
installed kernel, specify its exact release; do not guess from the package
version. For a new upstream version, explicitly select its matching tag:

```bash
./build.py --kernel 7.3.0-29-omarchy
# Example only, once this kernel and its headers are installed:
./build.py --kernel 7.3.1-1-omarchy --source-ref v7.3.1
```

The build first compiles the **unpatched** module and compares its entire
executable `.text` section with the original packaged module. If they differ,
it stops: inspect Omarchy's recipe and use the exact vendor sources/patches
before proceeding. A version number alone does not establish compatibility.
Then it applies the fix, rebuilds, and checks kernel vermagic and imported
symbol CRCs. If the fix is already upstream, patch application stops; review
whether this workaround is still needed.

Outputs go to `release/<kernel>/`, with SHA-256, source commit, source version,
and validation results in `build-info.json`. Evidence stays under `build/`.
An existing release is never overwritten; move it aside deliberately before
rebuilding that same kernel. Build success does not mean runtime success.

## Install and verify

Save your work and use a local terminal. Installation uses `sudo`:

```bash
./manage.sh install
# Reboot normally when ready, then:
./manage.sh verify
```

The command selects the running kernel's generated release. It verifies its
checksum and version, refuses kernel lockdown, and adds:

```text
/usr/lib/modules/<kernel>/updates/mt7925e.ko
```

It runs `depmod` and refreshes that kernel package's UKI/initramfs and boot
entry through `limine-mkinitcpio`. **It does not reload Wi-Fi or reboot.**
The original packaged module is left untouched. No packages, firmware,
sleep settings, or kernel version are changed. This does not create a boot
fallback or snapshot. If the boot-image refresh reports an error, repair it
before rebooting; the output is retained under `/var/tmp/mt7925-boot-refresh-*`.

The module is unsigned. It can load with lockdown disabled and module-signing
enforcement off; loading it may produce an unsigned-module warning. This
project does not change Secure Boot or trust settings.

If the current boot already suffered a driver oops, installing the file cannot
release the stuck kernel locks. Normal reboot may still hang. Avoid further
suspend or driver-reload attempts in that damaged boot.

Expected patched srcversion for the original `7.3.0-26-omarchy` build:
`1997298F9F9BAB7E45C8AF9`. Later builds get their own generated value.

## Rollback

```bash
./manage.sh rollback
# Reboot when ready.
```

This removes only the override matching that release's checksum, runs
`depmod`, and refreshes the boot image. Keep the local release metadata until
rollback is complete. A different override is never silently removed.

## Kernel upgrades

This is **not DKMS**: there is no automatic rebuild. After upgrading, build
against the new installed kernel and headers, boot that kernel, and install
its matching release. The installer deliberately refuses installation from
a different running kernel. An old kernel's override cannot repair a new one.
Keep suspend disabled/unused until the fix is active. Once the official
kernel contains the fix, retire the workaround rather than reapplying it.

## Git contents and local artifacts

Track only this README, build/install scripts, source lock, patch, and
Git settings. The Linux checkout (`.cache/`), build trees, modules, manifests,
validation binaries, logs, and earlier investigation downloads are ignored.
The project does not publish any files or create a remote automatically.
