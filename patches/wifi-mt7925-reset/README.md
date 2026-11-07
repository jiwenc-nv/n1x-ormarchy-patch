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

## Build and manage

Run the shared manager from the repository root:

```bash
./manage.sh build wifi-mt7925-reset
./manage.sh verify
./manage.sh apply wifi-mt7925-reset
./manage.sh status wifi-mt7925-reset --require-active
./manage.sh rollback wifi-mt7925-reset
```

See the [repository README](../../README.md) for target selection, boot-image
refresh, rollback, and adding patches. `target.json` is authoritative for the
kernel release, package, and upstream source commit.

The builder compiles the **unpatched** driver and compares its entire `.text`
section with the stock installed module. A mismatch stops the build. Then it
applies `fix.patch`, rebuilds, checks vermagic and imported symbol CRCs, and
writes `release/<kernel>/build-info.json`. Build evidence stays under `build/`;
downloaded source stays under `.cache/`. These local files are ignored by Git.
An existing release is never overwritten.

Requirements: matching installed kernel headers, GCC, make, git, Python 3,
patch, kmod, binutils, zstd (or the stock module's decompressor), and kernel
build tools including pahole for BTF. Source downloads require network access.
Build as your normal user. The module is unsigned; this project does not alter
Secure Boot or module-signature enforcement.

The original `7.3.0-26-omarchy` build has srcversion
`1997298F9F9BAB7E45C8AF9`. Its artifact was retained during the directory
migration. `build-info.legacy.json` preserves its original metadata; the
migration added the kernel package and the current tracked patch's checksum,
without claiming a new build or runtime test.

If a boot has already experienced the reset-path kernel oops, installing a
module cannot release stuck kernel locks. A reboot is still required; even
normal shutdown may hang in that damaged boot. This patch addresses the crash,
not the original cause of spontaneous wakeups.
