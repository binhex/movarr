# Post-Process Hooks — Design Spec

**Date:** 2026-05-08
**Branch:** dev
**Config version:** 2.13.0 → 2.14.0

---

## Problem

The `delete_lower_quality` feature calls `os.unlink()` to remove superseded library
files. On systems where media files are made immutable via `chattr +i`, this fails
silently with a permission error. There is also a forward-looking need to run
external tools (e.g. trimarr) against the destination directory after a successful
copy.

---

## Solution

A `PostProcessHooksConfig` block nested under `post_process.hooks` in `config.yaml`.
Each field is an optional shell command string. Three events are exposed, covering
the two concrete use cases and their natural complement.

---

## Config Shape

```yaml
post_process:
  hooks:
    post_copy:   "trimarr --language eng --media-path {dir} --no-backup"
    pre_delete:  "chattr -i {dir}/*"
    post_delete: "chattr +i {dir}/*"
```

All three fields default to `""` (disabled). An empty string means the hook is
skipped entirely — no subprocess is spawned.

### Events

| Field | Fires when | `{dir}` value |
|---|---|---|
| `post_copy` | All files from a torrent are successfully copied to the library | Destination movie directory |
| `pre_delete` | The deletion pass is about to start | Movie directory containing superseded files |
| `post_delete` | The deletion pass ran to completion without being aborted by a failed `pre_delete` hook (fires even if 0 files were deleted) | Same directory as `pre_delete` |

`post_copy` fires only when all files in the torrent copied without error. It does not
fire on partial copy success. It fires regardless of whether `delete_lower_quality`
is enabled — copying and deletion are independent features.

`pre_delete` and `post_delete` only fire when `delete_lower_quality: true`.

### Placeholders

`{dir}` is substituted with the resolved absolute path of the destination directory
(e.g. ``/media/movies/HD/Paul/The Matrix (1999)``).

`{leaf}` is substituted with the last path component of that directory
(e.g. ``The Matrix (1999)``).

Both placeholders are shell-quoted before substitution. Using `{dir}/*` in the
command relies on shell glob expansion, which is why ``shell=True`` is used
(see Security note below).

---

## New Model

```python
class PostProcessHooksConfig(BaseModel):
    post_copy: str = ""
    pre_delete: str = ""
    post_delete: str = ""
```

`PostProcessConfig` gains:

```python
hooks: PostProcessHooksConfig = Field(default_factory=PostProcessHooksConfig)
```

---

## `_run_hook` Helper

A single private function in `post_processor.py`:

```python
def _run_hook(command: str, dir_path: str, label: str) -> bool:
```

Behaviour:

1. Substitute `{dir}` with `dir_path` in `command`.
2. Run via `subprocess.Popen(cmd, shell=True, ...)` with `timeout=hook_timeout_secs` (converted from `hook_timeout_mins` config value; 0 disables timeout).
3. Log the command and exit code at INFO.
4. Log stdout/stderr at DEBUG (non-empty only).
5. Return `True` if exit code is 0, `False` otherwise.

A `TimeoutExpired` exception is caught, logged as an error, and treated as failure
(`False`).

### Failure Semantics

Hooks are best-effort except where failure makes the subsequent operation pointless:

| Hook | On failure |
|---|---|
| `post_copy` | Log warning, continue — trimarr failure does not undo a successful copy |
| `pre_delete` | Abort the deletion pass, return 0 deleted — if `chattr` failed, `unlink` will also fail; there is no point proceeding |
| `post_delete` | Log warning, continue — re-locking is best-effort |

### Security Note

`shell=True` is required so that glob patterns such as `chattr -i {dir}/*` are
expanded by the shell. The command is read exclusively from the user's own config
file, so the trust boundary is the same as the rest of the configuration. This
is the same model used by Radarr, Sonarr, and similar tools for custom scripts.
Do not expose this setting in multi-tenant environments.

---

## Call Sites

### `post_copy`

In `_process_one`, after `db.mark_completed()` and after all file copies have
succeeded:

```python
if config.post_process.hooks.post_copy:
    _run_hook(config.post_process.hooks.post_copy, dst_dir, "post_copy")
```

### `pre_delete` / `post_delete`

In `_delete_superseded_files`, after both safety guards pass:

```python
if config.post_process.hooks.pre_delete:
    if not _run_hook(config.post_process.hooks.pre_delete, dst_dir, "pre_delete"):
        logger.error("pre_delete hook failed; aborting deletion pass.")
        return 0

# ... deletion loop ...

if config.post_process.hooks.post_delete:
    _run_hook(config.post_process.hooks.post_delete, dst_dir, "post_delete")
```

---

## Migration

`_migrate_v213_to_v214` adds `post_process.hooks: {}` to any config that lacks it.
All three hook fields default to `""` in the new model, so existing configs gain
the block with all hooks disabled.

`_CONFIG_VERSION` bumps to `"2.14.0"`. `MIGRATIONS["2.13.0"]` is added.

The chain-migration test assertion for the final version is updated from `"2.13.0"`
to `"2.14.0"`.

---

## Testing

### `_run_hook`

- Returns `True` and logs correctly on exit code 0.
- Returns `False` on non-zero exit code.
- `{dir}` placeholder is substituted correctly.
- `TimeoutExpired` is caught and returns `False`.

### Failure semantics

- `pre_delete` hook failure causes `_delete_superseded_files` to return 0 and
  log an error; no files are deleted.
- `post_copy` hook failure does not affect the return value of `_process_one`.
- `post_delete` hook failure does not affect the deleted-file count.

### Hook not configured

- Empty string for any hook: no subprocess is spawned, no log output.

### Config migration

- `2.13.0` config gains `post_process.hooks` with all fields `""` after migration.
- Chain migration from the earliest version resolves to `"2.14.0"`.
