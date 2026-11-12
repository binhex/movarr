# Deferred findings — replace path hardening (Unraid shfs)

**Change:** `src/movarr/file_utils.py` replace path — post-swap confirmation, staging-leftover cleanup,
actionable `chattr` diagnostics. Reviewed in four dual-model review rounds on 2026-11-12.

**Status:** every HIGH/MEDIUM finding from those rounds was fixed in the change. The items below are residuals of
the *mitigations* (not of the failure being fixed), deliberately left out of scope.

## 1. Concurrent `--once` run can delete a live staging file

- **Where:** `_remove_stale_staging()` age guard; the `--once` mode documented in the README.
- **Finding:** cleanup removes `.<dst>.movarr-<8 hex>.part` leftovers older than two hours. A live copy refreshes
  the change time while writing, but the two full-file verification hashes between `copystat()` and
  `os.replace()` are read-only, so for a large remux on a slow array that quiet stretch can approach the
  threshold. A second movarr process replacing the same destination could then remove a staging file.
- **Why deferred:** post-processing runs with `max_instances=1`, so the supported operating mode cannot overlap
  with itself. Guarding against an operator running `--once` alongside the daemon needs a cross-process lock
  (flock on the staging file, or a pid-file liveness check) — a new mechanism, not a hardening fix.
- **How to close:** take an exclusive `flock` on the staging file when it is created and have cleanup skip
  candidates it cannot lock; or make `--once` refuse to start while the daemon pid file is live.

## 2. File systems that synthesise the change time from the modification time

- **Where:** `_remove_stale_staging()` age guard (`st_ctime`).
- **Finding:** on a mount that reports `st_ctime == st_mtime` (some rclone/FUSE mounts), a freshly finished
  staging file whose *source* has an old modification time can look old because `shutil.copystat()` copies
  that time onto it. A
  change time of `0` is treated as unknown and kept, which covers a mount that reports no change time at all
  but not one that reports `ctime == mtime`.
- **Why deferred:** the documented and tested targets (ext4/xfs array disks and Unraid shfs) report a real change
  time that `copystat()` necessarily refreshes. Hardening for synthesised change times needs an in-process
  in-flight registry keyed by staging path, which buys nothing while post-processing is single-threaded and
  single-instance.
- **How to close:** add a module-level in-flight set populated by `_replace_copy()` and consulted by
  `_remove_stale_staging()`; the age heuristic then only has to cover leftovers from previous runs.

## 3. Cleanup is destination-scoped

- **Where:** `_remove_stale_staging()` is only called for the destination being replaced.
- **Finding:** a leftover belonging to a title that is never retried (for example a torrent removed by hand
  after a failed swap) is never reclaimed, whatever the age threshold.
- **Why deferred:** reclaiming across the whole library needs a directory walk the post-processor does not
  currently perform, and the README already tells operators to clear a stale duplicate by hand. The volume at
  risk is one staging copy per abandoned title.
- **How to close:** sweep `<movie dir>/*.movarr-*.part` during the post-processing pass for each library root, or
  expose a maintenance command that does it.

## 4. Legacy staging names are not reclaimed

- **Where:** `_is_stale_staging()` requires the `movarr-` marker introduced in this change.
- **Finding:** the new matcher does not match unmarked leftovers (`.<dst>.<8 hex>.part`) left by releases up to
  and including v1.5.1, which shipped the swap without the marker; no release ever reclaimed leftovers.
- **Why deferred:** matching the unmarked shape is exactly what made a user file such as
  `.Movie.Name.mkv.20230101.part` deletable; the marker is what makes the cleanup safe.
- **How to close:** nothing to code — list any remaining unmarked leftovers once
  (`find /mnt/disk*/Movies -name '.*.part'`) and delete them by hand.

## 5. Staging names gain seven bytes

- **Where:** `_staging_name()`; the marker makes the name `.<dst>.movarr-<8 hex>.part`.
- **Finding:** for a destination name longer than roughly 233 bytes the staging `open()` now hits
  `ENAMETOOLONG` where the previous name fit. Failure is graceful: the copy fails, the destination is untouched
  and the cause is logged.
- **Why deferred:** real release names are far shorter, and deriving the token length from the remaining name
  budget adds branching for a case that cannot occur with sane library paths.
- **How to close:** shorten the token (or hash the destination name) when `len(dst.name)` exceeds a threshold.
