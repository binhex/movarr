# Plan: Change log_path, db_path, pid_path to directories (filenames hardcoded)

**Date:** 2026-05-14
**Answers from user:**

- Config path: CLI default only
- CLI overrides: directories only (`--log-path`, `--db-path`; `--config-path` default `configs`)
- Migration: auto-migrate existing configs
- pid_path: change it too (consistent)

## Changes

### 1. Add hardcoded filename constants (`config.py`)

The constants are now public (`LOG_FILENAME`, `DB_FILENAME`, `PID_FILENAME` and the private
`_CONFIG_FILENAME`) and are imported by
`logger.py`, `database.py` and `scheduler.py` — they are the single source of the literal names.

```python
_LOG_FILENAME = "movarr.log"
_DB_FILENAME = "movarr.db"
_PID_FILENAME = "movarr.pid"
_CONFIG_FILENAME = "movarr.yml"
```

### 2. Config model defaults (`config.py`)

- `log_path`: `"logs/movarr.log"` → `"logs"`
- `db_path`: `"db/movarr.db"` → `"db"`
- `pid_path`: `"configs/movarr.pid"` → `"pids"`

### 3. Migration (`config.py`)

New version bump. Strip known filenames from path values.

### 4. Consumers — construct full path from directory

- `logger.py:setup_logger()`: `log_path` → `<log_path>/movarr.log`
- `database.py:Database.__init__()`: `db_path` → `<db_path>/movarr.db`
- `scheduler.py:_write_pid()`: `pid_path` → `<pid_path>/movarr.pid`
- `config.py:load_config/create_default_config`: CLI default `configs` → `<config_dir>/movarr.yml`

### 5. CLI changes (`cli.py`)

**Not implemented as written** — the shipped options kept their original names and now take a
directory: `--config-path` (default `configs`), `--log-path`, `--db-path`, plus `--pid-path`.
The `--*-dir` renaming described here was never applied.

### Files touched

- `src/movarr/config.py`
- `src/movarr/cli.py`
- `src/movarr/logger.py`
- `src/movarr/database.py`
- `src/movarr/scheduler.py`
- `tests/unit/test_config.py`
- `tests/unit/test_cli.py`
- `tests/unit/test_logger.py`
