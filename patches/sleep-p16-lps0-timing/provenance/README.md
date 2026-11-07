# Reproduction records

The original source lock is preserved so existing `build-info.json` files
continue to identify the build that was tested. `../reproduction.lock.json`
adds checksummed records without rewriting that history.

`nvidia-source-checksums.json` was copied from the manifest made **before** the
original NVIDIA compilation. Every reconstructed file was compared with that
manifest. The pinned kernel recipe carries the same seven ARM patches as
`pkgbuilds/nvidia-open-dkms/PKGBUILD` at commit
`49250dc672819eb8b49727defa03de8b8f1c3e74`. The builder reproduces its `utils.mk`
normalization and patches, then supplies the package-generated `dkms.conf`.
That metadata is retained for exact source-tree comparison; DKMS is not run.

`baseline-audit.json` was computed from the original installed kernel's
module paths and `build/Module.symvers`, and the original patched MT7925
module's `.text`. Each was also compared with the completed test build.
Canonical hashes use UTF-8 JSON with sorted keys, separators `(',', ':')`,
and no trailing newline. Module paths are a sorted unique list relative to
`kernel/`, ending in `.ko` with compression extensions removed. Exported
symbols map symbol names to the CRC strings in `Module.symvers`. These
records allow the same comparisons after the original packages are removed.

`build-environment.json` records native build prerequisites and relevant
boot-tool versions. Exact host compiler packages are checked by `doctor`;
the locally extracted tool packages remain pinned in `source.lock.json`.

`initramfs/` is a reference snapshot of the original host inputs, including
the two N1x early-panel-brightness hook files. Its `inputs.json` maps original
absolute filenames to hashes; copies are stored under the corresponding
relative paths. These files contain no disk identifiers or encryption keys.
Review them alongside the destination Omarchy configuration; the build and
installation workflow does not blindly copy them into `/etc`.
