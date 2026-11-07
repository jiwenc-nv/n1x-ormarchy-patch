# N1x Omarchy patches

A collection of local patches with one shared kernel target and one management
command. Each patch keeps its source, builder, and per-kernel releases in its
own folder. Currently includes the MT7925 Wi-Fi reset crash fix.

```text
target.json                         Shared kernel/package/source commit and patch order
manage.sh                           CLI entry point
patchctl.py                         Verification, status, apply, rollback
patches/
  wifi-mt7925-reset/
    patch.json                      Patch ID, backend, module, stock location
    source.lock.json                Pinned source and patch provenance
    fix.patch
    build.py
    README.md
    release/<kernel>/               Module and build-info.json (local, ignored)
    build/                          Build evidence (local, ignored)
    .cache/                         Downloaded source (local, ignored)
tests/                              Tests against a disposable fake system
```

## Commands

Run from this directory:

```bash
./manage.sh list
./manage.sh verify
./manage.sh status
./manage.sh status --require-active

./manage.sh build                    # Build all registered patches as your normal user
./manage.sh apply --dry-run          # Check the full set without changing the system
./manage.sh apply                    # Elevates through sudo; installs all patches
./manage.sh rollback --dry-run
./manage.sh rollback                 # Removes owned overrides in reverse patch order
```

`apply`, `rollback`, `status`, `list`, and `build` also accept patch IDs:

```bash
./manage.sh apply wifi-mt7925-reset
./manage.sh rollback wifi-mt7925-reset
./manage.sh status wifi-mt7925-reset --json
./manage.sh build wifi-mt7925-reset --jobs 4
```

`verify` always checks **every registered patch**, even if IDs are supplied.
A selected `apply` also requires the entire registered release set to verify,
so a stale second patch cannot silently slip through an upgrade.

`./manage.sh install` remains an alias for `apply`; `./build.py` delegates to
the master `build` command. Kernel selection now comes from `target.json`,
rather than a positional kernel argument or an implicit `uname -r` default.

## Verification versus status

`verify` checks source pins, artifact kernel release/package, fix commit, patch
and module SHA-256, actual module vermagic/srcversion, and the builder's baseline
machine-code and symbol-CRC validation records. Missing or stale artifacts fail
verification. It does not download, rebuild, or establish runtime correctness.

`status` reports three separate facts:

- **disk:** `applied`, `absent`, or `conflict` at the managed override path.
- **selected:** whether kmod will select that override for the target kernel.
- **loaded:** whether the running module's srcversion matches the patched build;
  otherwise `different`, `not-loaded`, or `other-kernel`.

An applied file can still need a reboot. After rollback, a patched module may
remain loaded until reboot. `status --require-active` exits 2 if a selected
patch is not active; conflicting/unknown state exits 1. `verify` exits 1 on
any mismatch. `list`, `status`, and `verify` support `--json` for automation.
Srcversion identifies the loaded build; it is not a runtime functional test.

## Applying and rolling back

Apply/rollback require the exact configured kernel to be running and its
installed `pkgbase` to match the configured kernel package. Apply also checks
lockdown and module-signature enforcement. Operations use a shared exclusive
lock, preflight all registered overrides, and refuse unknown overrides or
conflicting replacements for the same module.

The module backend writes only:

```text
/usr/lib/modules/<kernel>/updates/<module>.ko
```

Stock packaged modules remain in place. After changing the selected files, the
manager runs `depmod` once, verifies module selection, and refreshes the
package's boot images once via `limine-mkinitcpio`. It checks both the exit code
and reported `ERROR:` lines. It never reloads drivers or reboots the machine.

On a handled failure, it restores the previous override files and reruns
`depmod`; if boot refresh started, it also attempts to rebuild the previous
boot images. Boot logs and failed-operation backups are retained under
`/var/tmp/n1x-patches-*`. An incomplete recovery is explicitly reported and
must be repaired before reboot. This is not power-loss-atomic across files and
boot images; interrupted operations can be repaired by reapplying or rolling
back the same release.

Keep `release/<kernel>/build-info.json` until rollback is complete. Rollback
uses its recorded checksum to establish ownership; it does not require the
artifact or current patch sources to be present. Retain the old `target.json`
and source locks alongside old releases when moving to another target.

## Kernel upgrades

This is not DKMS and does not install kernels or automatically rebuild.

1. Retain the old target/release metadata for recovery.
2. Install the new kernel and matching headers through the normal OS workflow.
3. Update `target.json` with the exact kernel release, package, and source
   commit. Update every patch's `source.lock.json` to match that source commit
   and its corresponding source ref. Review whether each fix is still needed.
4. Run `./manage.sh build`, then `./manage.sh verify`. A build can target an
   installed kernel before it is running. The Wi-Fi builder stops if the
   unpatched source does not reproduce the packaged driver's machine code.
5. Boot the target kernel, apply the verified set, then reboot and run
   `./manage.sh status --require-active`.

The install workflow still requires booting the target kernel before applying;
it does not promise that patches are active on the first boot of a new kernel.
An override in an old kernel's directory does not patch a new kernel.

## Adding a patch

Create `patches/<id>/` with `patch.json`, `source.lock.json`, `fix.patch`,
`build.py`, and a README covering purpose/provenance. Register its ID in the
ordered `patches` array in `target.json`. Unregistered manifest folders are
reported as configuration errors rather than silently skipped.

The current backend is **`kernel-module`**. Its manifest follows the Wi-Fi
example and names one module and its stock path relative to the kernel module
directory. Its builder accepts `--kernel`, `--kernel-package`, and `--jobs`,
and writes `release/<kernel>/<module>.ko` plus `build-info.json` containing:

```text
kernel, kernel_package, upstream_commit, fix_commit, patch_sha256,
module_sha256, module_srcversion,
stock_text_matches_unpatched_build: true, import_symbol_crcs_match: true
```

Each builder is responsible for performing those checks; copy the Wi-Fi
builder only when its source/build method fits the new driver. The manager
rechecks artifact identity and compatibility before installation. Two fixes
touching the same module must be built together as one patch entry.

Whole-kernel changes, firmware changes, and system configuration edits require
a separate backend with an appropriate verification/rollback contract.
Unsupported kinds are rejected. No experimental sleep fix is included merely
because the repository now supports multiple entries.

## Validation

```bash
python -m unittest discover -s tests -v
./manage.sh verify
./manage.sh status --require-active
./manage.sh apply --dry-run
./manage.sh rollback --dry-run
```

Tests use temporary files and a fake module/boot environment. They cover stale
targets and artifacts, source drift, unrelated overrides, loaded-versus-disk
state, batch preflight, and recovery after depmod or boot-image failures. They
do not install modules, refresh real boot images, or exercise real suspend.
