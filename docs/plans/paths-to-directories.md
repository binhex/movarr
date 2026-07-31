# Plan: Change log_path, db_path, pid_path to directories (filenames hardcoded)

**Date:** 2026-05-14
**Answers from user:**
- Config path: CLI default only
- CLI overrides: directories only (--log-dir, --db-dir)
- Migration: auto-migrate existing configs
- pid_path: change it too (consistent)

## Changes

### 1. Add hardcoded filename constants (`config.py`)
```python
_LOG_FILENAME = "movarr.log"
_DB_FILENAME = "movarr.db"
_PID_FILENAME = "movarr.pid"
_CONFIG_FILENAME = "movarr.yml"
```

### 2. Config model defaults (`config.py`)
- `log_path`: `"logs/movarr.log"` → `"logs"`
- `db_path`: `"db/movarr.db"` → `"db"`
- `pid_path`: `"configs/movarr.pid"` → `"configs"`

### 3. Migration (`config.py`)
New version bump. Strip known filenames from path values.

### 4. Consumers — construct full path from directory
- `logger.py:setup_logger()`: `log_path` → `<log_path>/movarr.log`
- `database.py:Database.__init__()`: `db_path` → `<db_path>/movarr.db`
- `scheduler.py:_write_pid()`: `pid_path` → `<pid_path>/movarr.pid`
- `config.py:load_config/create_default_config`: CLI default `configs` → `<config_dir>/movarr.yml`

### 5. CLI changes (`cli.py`)
- `--config-path` → `--config-dir` (default: `configs`)
- `--log-path` → `--log-dir`
- `--db-path` → `--db-dir`

### Files touched
- `src/movarr/config.py`
- `src/movarr/cli.py`
- `src/movarr/logger.py`
- `src/movarr/database.py`
- `src/movarr/scheduler.py`
- `tests/unit/test_config.py`
- `tests/unit/test_cli.py`
- `tests/unit/test_logger.py`
