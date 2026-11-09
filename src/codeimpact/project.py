from __future__ import annotations

import fnmatch
import os
import pickle
from dataclasses import dataclass
from pathlib import Path
from time import perf_counter
from typing import Dict, Iterable, List, Optional, Sequence, Set, Tuple

from . import __version__
from .graph import Graph, Unresolved, build_graph
from .models import CLASS, FUNCTION, METHOD, ModuleInfo, Symbol
from .parser import parse_source
from .resolver import Resolver

DEFAULT_TEST_PATTERNS: Tuple[str, ...] = ("test_*.py", "*_test.py")
SKIP_DIRS = frozenset(
    {
        "__pycache__", "build", "dist", "node_modules", "site-packages",
        "venv", "env", "htmlcov", "__pypackages__",
    }
)
CACHE_DIR = ".codeimpact_cache"
CACHE_KEY = f"{__version__}:2"


@dataclass
class Settings:
    root: Path
    exclude: Sequence[str] = ()
    test_patterns: Sequence[str] = DEFAULT_TEST_PATTERNS
    fuzzy: int = 1
    use_cache: bool = True


def discover(root: Path, exclude: Sequence[str] = ()) -> List[Path]:
    found: List[Path] = []
    for directory, dirnames, filenames in os.walk(root):
        base = Path(directory)
        relative = base.relative_to(root)
        dirnames[:] = sorted(
            d for d in dirnames if not _skip_dir(d) and not _excluded(relative / d, exclude)
        )
        for name in sorted(filenames):
            if name.endswith(".py") and not _excluded(relative / name, exclude):
                found.append(base / name)
    return found


def _skip_dir(name: str) -> bool:
    return name in SKIP_DIRS or name.startswith(".") or name.endswith(".egg-info")


def _excluded(relative: Path, patterns: Sequence[str]) -> bool:
    posix = relative.as_posix()
    for pattern in patterns:
        cleaned = pattern.strip().rstrip("/")
        if not cleaned:
            continue
        if fnmatch.fnmatch(posix, cleaned) or fnmatch.fnmatch(relative.name, cleaned):
            return True
    return False


def module_name(path: Path, root: Path) -> str:
    parts: List[str] = [] if path.name == "__init__.py" else [path.stem]
    parent = path.parent
    while (parent / "__init__.py").is_file():
        parts.insert(0, parent.name)
        if parent == root or parent.parent == parent:
            break
        parent = parent.parent
    return ".".join(parts) if parts else path.parent.name


def assign_names(root: Path, files: Iterable[Path]) -> List[Tuple[Path, str]]:
    ordered = sorted(files, key=lambda p: (len(p.relative_to(root).parts), p.as_posix()))
    used: Set[str] = set()
    named: List[Tuple[Path, str]] = []
    for path in ordered:
        name = module_name(path, root)
        if name in used:
            parts = list(path.relative_to(root).with_suffix("").parts)
            if parts and parts[-1] == "__init__":
                parts = parts[:-1]
            base = ".".join(parts) or path.stem
            name, counter = base, 2
            while name in used:
                name = f"{base}~{counter}"
                counter += 1
        used.add(name)
        named.append((path, name))
    return named


class _Cache:
    def __init__(self, root: Path) -> None:
        self.directory = root / CACHE_DIR
        self.file = self.directory / "modules.pickle"
        self.entries: Dict[str, Tuple[tuple, ModuleInfo]] = {}
        self.dirty = False
        try:
            with self.file.open("rb") as handle:
                data = pickle.load(handle)
            if isinstance(data, dict) and data.get("key") == CACHE_KEY:
                self.entries = data.get("entries", {})
        except Exception:
            self.entries = {}

    def get(self, key: str, stamp: tuple) -> Optional[ModuleInfo]:
        entry = self.entries.get(key)
        if entry is not None and entry[0] == stamp:
            return entry[1]
        return None

    def put(self, key: str, stamp: tuple, info: ModuleInfo) -> None:
        self.entries[key] = (stamp, info)
        self.dirty = True

    def save(self, keep: Set[str]) -> None:
        stale = set(self.entries) - keep
        for key in stale:
            del self.entries[key]
        if not (self.dirty or stale):
            return
        try:
            self.directory.mkdir(exist_ok=True)
            ignore = self.directory / ".gitignore"
            if not ignore.exists():
                ignore.write_text("*\n", encoding="utf-8")
            temporary = self.file.with_suffix(".tmp")
            with temporary.open("wb") as handle:
                pickle.dump({"key": CACHE_KEY, "entries": self.entries}, handle, protocol=pickle.HIGHEST_PROTOCOL)
            os.replace(temporary, self.file)
        except OSError:
            pass


class Project:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.root = Path(settings.root).resolve()
        self.modules: Dict[str, ModuleInfo] = {}
        self.by_path: Dict[str, ModuleInfo] = {}
        self.errors: Dict[str, str] = {}
        self.resolver = Resolver({}, settings.fuzzy)
        self.graph = Graph()
        self.unresolved: Unresolved = {}
        self.elapsed = 0.0

    @classmethod
    def load(cls, settings: Settings) -> "Project":
        project = cls(settings)
        project._load()
        return project

    def _load(self) -> None:
        started = perf_counter()
        files = discover(self.root, self.settings.exclude)
        cache = _Cache(self.root) if self.settings.use_cache else None
        keep: Set[str] = set()
        for path, name in assign_names(self.root, files):
            try:
                stat = path.stat()
            except OSError:
                continue
            rel = path.relative_to(self.root).as_posix()
            is_test = self.is_test_path(path)
            stamp = (stat.st_mtime_ns, stat.st_size, name, is_test)
            info = cache.get(rel, stamp) if cache else None
            if info is None:
                try:
                    source = path.read_bytes()
                except OSError:
                    continue
                info = parse_source(
                    source,
                    name,
                    str(path),
                    rel,
                    is_package=path.name == "__init__.py",
                    is_test=is_test,
                    is_conftest=path.name == "conftest.py",
                )
                if cache:
                    cache.put(rel, stamp, info)
            info.path = str(path)
            keep.add(rel)
            self.modules[name] = info
            self.by_path[str(path)] = info
            if info.error:
                self.errors[rel] = info.error
        if cache:
            cache.save(keep)
        self.resolver = Resolver(self.modules, self.settings.fuzzy)
        self.graph, self.unresolved = build_graph(self.modules, self.resolver, self.root)
        self.elapsed = perf_counter() - started

    def is_test_path(self, path: Path) -> bool:
        return any(fnmatch.fnmatch(path.name, pattern) for pattern in self.settings.test_patterns)

    def find_modules(self, text: str) -> List[ModuleInfo]:
        candidates = []
        raw = Path(text).expanduser()
        if raw.is_absolute():
            candidates.append(raw)
        else:
            candidates.extend([Path.cwd() / raw, self.root / raw])
        for candidate in candidates:
            try:
                resolved = candidate.resolve()
            except OSError:
                continue
            if resolved.is_dir():
                found = [
                    info
                    for info in self.modules.values()
                    if Path(info.path) == resolved or resolved in Path(info.path).parents
                ]
                if found:
                    return sorted(found, key=lambda i: i.rel)
            info = self.by_path.get(str(resolved))
            if info is not None:
                return [info]
        if text in self.modules:
            return [self.modules[text]]
        return []

    def symbol(self, qualname: str) -> Optional[Symbol]:
        return self.resolver.symbols.get(qualname)

    def nodeid(self, symbol: Symbol) -> Optional[str]:
        info = self.modules.get(symbol.module)
        if info is None or not info.is_test or not symbol.local:
            return None
        name = symbol.name
        if symbol.kind == FUNCTION and name.startswith("test"):
            pass
        elif symbol.kind == METHOD and name.startswith("test"):
            parent = self.resolver.symbols.get(symbol.parent or "")
            if parent is None or not parent.name.startswith("Test"):
                return None
        elif symbol.kind == CLASS and name.startswith("Test"):
            pass
        else:
            return None
        return f"{info.rel}::{symbol.local.replace('.', '::')}"
