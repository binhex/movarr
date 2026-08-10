# Config Migration System — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a versioned config migration system so that existing `config.yml` files are automatically upgraded on startup when the schema changes.

**Architecture:** A `MIGRATIONS` dict maps each old version string to a transform function. `_run_migrations()` walks the chain from the file's current version to `_CONFIG_VERSION`, backing up the file first, then writes the migrated YAML back to disk. `load_config()` calls `_run_migrations()` immediately after reading the raw YAML.

**Tech Stack:** Python 3.12, pydantic v2, PyYAML, loguru, pytest, uv

---

## File Map

| File | Change |
|------|--------|
| `src/movarr/config.py` | Advance `_CONFIG_VERSION` → `"2.0.0"`, add `shutil`/`Callable` imports, add `_migrate_v1_to_v2()`, `MIGRATIONS` dict, `_run_migrations()`, call it in `load_config()` |
| `tests/unit/test_config.py` | Add `TestConfigMigration` class with 5 tests covering the full migration surface |

---

### Task 1: Write failing migration tests

**Files:**
- Modify: `tests/unit/test_config.py`

- [ ] **Step 1: Add `TestConfigMigration` class with failing tests**

Append to `tests/unit/test_config.py` (after `TestLoadConfig`):

```python
import yaml  # add at top of file with other imports


# ---------------------------------------------------------------------------
# Config migration
# ---------------------------------------------------------------------------


class TestConfigMigration:
    """load_config must auto-migrate older config schemas on startup."""

    def _v1_config(self, tmp_path: Path) -> Path:
        """Write a v1.0.0 config (with notification.email block) to tmp_path."""
        cfg_file = tmp_path / "config.yml"
        cfg_file.write_text(
            "general:\n"
            "  config_version: '1.0.0'\n"
            "notification:\n"
            "  email:\n"
            "    enabled: false\n"
            "    host: smtp.example.com\n"
            "    port: 587\n"
        )
        return cfg_file

    def test_v1_config_is_migrated_to_v2(self, tmp_path: Path) -> None:
        """A v1.0.0 config with notification.email is migrated to v2.0.0."""
        cfg_file = self._v1_config(tmp_path)
        cfg = load_config(str(cfg_file))
        assert cfg.general.config_version == "2.0.0"
        assert cfg.notification.apprise_urls == []

    def test_v1_migration_removes_email_from_disk(self, tmp_path: Path) -> None:
        """After migration the on-disk YAML no longer contains notification.email."""
        cfg_file = self._v1_config(tmp_path)
        load_config(str(cfg_file))
        raw = yaml.safe_load(cfg_file.read_text())
        assert "email" not in raw.get("notification", {})
        assert "apprise_urls" in raw.get("notification", {})

    def test_v1_migration_creates_backup(self, tmp_path: Path) -> None:
        """A backup file config.yml.bak.1.0.0 is created before migration."""
        cfg_file = self._v1_config(tmp_path)
        load_config(str(cfg_file))
        backup = tmp_path / "config.yml.bak.1.0.0"
        assert backup.exists()
        # Backup should still contain the old email block
        raw = yaml.safe_load(backup.read_text())
        assert "email" in raw.get("notification", {})

    def test_no_version_key_treated_as_v1(self, tmp_path: Path) -> None:
        """A config without general.config_version is treated as v1.0.0 and migrated."""
        cfg_file = tmp_path / "config.yml"
        cfg_file.write_text(
            "notification:\n"
            "  email:\n"
            "    enabled: false\n"
        )
        cfg = load_config(str(cfg_file))
        assert cfg.general.config_version == "2.0.0"

    def test_v2_config_needs_no_migration(self, tmp_path: Path) -> None:
        """A v2.0.0 config is loaded without creating a backup file."""
        cfg_file = tmp_path / "config.yml"
        cfg_file.write_text(
            "general:\n"
            "  config_version: '2.0.0'\n"
            "notification:\n"
            "  apprise_urls: []\n"
        )
        load_config(str(cfg_file))
        backup = tmp_path / "config.yml.bak.2.0.0"
        assert not backup.exists()
```

- [ ] **Step 2: Run tests to confirm RED**

```bash
cd /data/movarr && uv run pytest tests/unit/test_config.py::TestConfigMigration -v --tb=short 2>&1 | tail -20
```

Expected: 5 failures — `"NotificationConfig" object has no field "apprise_urls"` or similar (migration code not yet present).

- [ ] **Step 3: Commit failing tests**

```bash
cd /data/movarr && git add tests/unit/test_config.py && git commit -m "test: add failing tests for config migration system"
```

---

### Task 2: Implement migration in config.py

**Files:**
- Modify: `src/movarr/config.py`

- [ ] **Step 1: Add imports at the top of config.py**

Current imports section:
```python
from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field, field_validator
```

Replace with:
```python
from __future__ import annotations

import shutil
from collections.abc import Callable
from pathlib import Path
from typing import Any

import yaml
from loguru import logger
from pydantic import BaseModel, Field, field_validator
```

- [ ] **Step 2: Advance _CONFIG_VERSION constant**

Change:
```python
_CONFIG_VERSION = "1.0.0"
```

To:
```python
_CONFIG_VERSION = "2.0.0"
```

This makes new configs write `general.config_version: "2.0.0"` by default.

- [ ] **Step 3: Add migration functions and registry after the `_CONFIG_VERSION` line**

Add after `_CONFIG_VERSION = "2.0.0"`:

```python


def _migrate_v1_to_v2(raw: dict[str, Any]) -> dict[str, Any]:
    """Migrate v1.0.0 → v2.0.0: replace notification.email with apprise_urls."""
    notification = raw.setdefault("notification", {})
    notification.pop("email", None)
    notification.setdefault("apprise_urls", [])
    raw.setdefault("general", {})["config_version"] = "2.0.0"
    return raw


MIGRATIONS: dict[str, Callable[[dict[str, Any]], dict[str, Any]]] = {
    "1.0.0": _migrate_v1_to_v2,
}
```

- [ ] **Step 4: Add _run_migrations() helper before `_default_config_dict()`**

Insert before `def _default_config_dict()`:

```python
def _run_migrations(raw: dict[str, Any], config_path: Path) -> dict[str, Any]:
    """Apply any pending schema migrations to *raw*, updating the file on disk.

    A backup of the original file is created before the first migration step.
    Migrations are applied sequentially until the config reaches ``_CONFIG_VERSION``.

    Args:
        raw: The raw config dict loaded from YAML.
        config_path: Path to the config file (used for backup and overwrite).

    Returns:
        The migrated raw dict.
    """
    current = raw.get("general", {}).get("config_version", "1.0.0")
    if current not in MIGRATIONS:
        return raw

    backup_path = config_path.with_suffix(f".yml.bak.{current}")
    try:
        shutil.copy2(config_path, backup_path)
        logger.info("Config backup created at {}", backup_path)
    except OSError:
        logger.warning("Could not create config backup at {}; proceeding without backup.", backup_path)

    while current in MIGRATIONS:
        previous = current
        raw = MIGRATIONS[current](raw)
        current = raw.get("general", {}).get("config_version", current)
        logger.info("Config migrated from v{} to v{}", previous, current)

    with config_path.open("w", encoding="utf-8") as fh:
        yaml.dump(raw, fh, default_flow_style=False, sort_keys=False)

    return raw
```

- [ ] **Step 5: Call _run_migrations() in load_config()**

In `load_config()`, after the `yaml.safe_load` line, add one call before `_deep_merge`:

```python
    with path.open("r", encoding="utf-8") as fh:
        raw: dict[str, Any] = yaml.safe_load(fh) or {}

    raw = _run_migrations(raw, path)          # ← add this line

    merged = _deep_merge(_default_config_dict(), raw)
    return Config.model_validate(merged)
```

- [ ] **Step 6: Run migration tests — verify GREEN**

```bash
cd /data/movarr && uv run pytest tests/unit/test_config.py::TestConfigMigration -v --tb=short 2>&1 | tail -20
```

Expected: 5 passed.

- [ ] **Step 7: Run full test suite**

```bash
cd /data/movarr && uv run pytest --tb=short -q 2>&1 | tail -10
```

Expected: all pass, coverage ≥ 97%.

- [ ] **Step 8: Run pre-commit**

```bash
cd /data/movarr && uv run pre-commit run --all-files 2>&1 | tail -15
```

Expected: all Passed.

- [ ] **Step 9: Commit implementation**

```bash
cd /data/movarr && git add src/movarr/config.py && git commit -m "feat: add versioned config migration system

- _CONFIG_VERSION advances from 1.0.0 to 2.0.0
- MIGRATIONS registry maps version string to transform function
- _run_migrations() applies chain migrations and rewrites config on disk
- Backup created at config.yml.bak.<old_version> before first migration
- v1.0.0 -> v2.0.0: removes notification.email block, adds apprise_urls
- load_config() calls _run_migrations() before deep-merge/validation
- Existing configs auto-upgraded transparently on next startup"
```
