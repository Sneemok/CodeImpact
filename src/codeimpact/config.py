from __future__ import annotations

from pathlib import Path
from typing import Any, Dict

MARKERS = ("pyproject.toml", "setup.cfg", "setup.py", ".git")


class ConfigError(Exception):
    pass


def find_root(start: Path) -> Path:
    current = start.resolve()
    for candidate in (current, *current.parents):
        if any((candidate / marker).exists() for marker in MARKERS):
            return candidate
    return current


def _toml():
    try:
        import tomllib
        return tomllib
    except ImportError:
        try:
            import tomli
            return tomli
        except ImportError:
            return None


def read_config(root: Path) -> Dict[str, Any]:
    path = root / "pyproject.toml"
    if not path.is_file():
        return {}
    parser = _toml()
    if parser is None:
        return {}
    try:
        data = parser.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise ConfigError(f"cannot read {path}: {exc}") from exc
    section = data.get("tool", {}).get("codeimpact", {})
    if not isinstance(section, dict):
        raise ConfigError("[tool.codeimpact] must be a table")
    return section
