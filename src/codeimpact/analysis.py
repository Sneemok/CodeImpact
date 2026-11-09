from __future__ import annotations

import difflib
from collections import Counter, defaultdict, deque
from dataclasses import dataclass, field
from pathlib import Path
from time import perf_counter
from typing import Callable, Dict, Iterable, List, Optional, Sequence, Set, Tuple

from . import gitdiff
from .graph import Edge
from .models import EXACT, LIKELY, MODULE, ModuleInfo, Symbol
from .parser import parse_source
from .project import Project, module_name

IMPACT = "impact"
DEPS = "deps"


class SelectionError(Exception):
    pass


@dataclass
class Hit:
    symbol: Symbol
    depth: int
    via: str
    confidence: str
    line: Optional[int]
    nodeid: Optional[str] = None
    role: str = "code"

    @property
    def is_test(self) -> bool:
        return self.nodeid is not None

    @property
    def location(self) -> str:
        return f"{self.symbol.path}:{self.line or self.symbol.lineno}"


@dataclass
class Changes:
    changed: Dict[str, Symbol] = field(default_factory=dict)
    removed: Dict[str, Symbol] = field(default_factory=dict)
    added: Dict[str, Symbol] = field(default_factory=dict)
    files: List[str] = field(default_factory=list)


@dataclass
class Report:
    mode: str
    title: str
    root: str
    changed: List[Symbol]
    removed: List[Symbol]
    added: List[Symbol]
    hits: List[Hit]
    edges: List[Tuple[str, str, str]]
    tests: List[str]
    related_tests: List[str]
    stats: Dict[str, object]
    warnings: List[str] = field(default_factory=list)
    broken: List[Tuple[str, str]] = field(default_factory=list)

    @property
    def impacted(self) -> List[Hit]:
        return [hit for hit in self.hits if not hit.is_test]

    @property
    def modules(self) -> List[Tuple[str, str, int]]:
        code = [hit for hit in self.hits if hit.role == "code"]
        counts = Counter(hit.symbol.module for hit in code)
        paths = {hit.symbol.module: hit.symbol.path for hit in code}
        return sorted(((m, paths[m], n) for m, n in counts.items()), key=lambda item: (-item[2], item[0]))

    @property
    def max_depth(self) -> int:
        return max((hit.depth for hit in self.hits), default=0)

    @property
    def risk(self) -> str:
        impacted = len(self.impacted)
        modules = len(self.modules)
        if self.broken:
            return "high"
        if impacted == 0:
            return "none"
        if impacted <= 5 and modules <= 2:
            return "low"
        if impacted <= 25 and modules <= 6:
            return "medium"
        return "high"


def parse_range(text: str) -> Tuple[int, int]:
    cleaned = text.strip().replace(":", "-")
    try:
        if "-" in cleaned:
            start_text, end_text = cleaned.split("-", 1)
            start, end = int(start_text), int(end_text)
        else:
            start = end = int(cleaned)
    except ValueError as exc:
        raise SelectionError(f"invalid line range '{text}', expected N or A-B") from exc
    if start < 1 or end < start:
        raise SelectionError(f"invalid line range '{text}'")
    return start, end


def symbols_for_lines(info: ModuleInfo, lines: Iterable[int]) -> List[Symbol]:
    ordered = sorted(info.members(), key=lambda s: (s.span, -s.lineno))
    found: Dict[str, Symbol] = {}
    for line in lines:
        for symbol in ordered:
            if symbol.covers(line):
                found.setdefault(symbol.qualname, symbol)
                break
        else:
            found.setdefault(info.name, info.module_symbol)
    return list(found.values())


def _matches(symbol: Symbol, wanted: str) -> bool:
    return symbol.local == wanted or symbol.qualname == wanted or symbol.local.endswith("." + wanted)


def select(
    project: Project,
    specs: Sequence[str],
    names: Sequence[str] = (),
    ranges: Sequence[str] = (),
) -> List[Symbol]:
    if not specs:
        raise SelectionError("no target given")
    spans = [parse_range(r) for r in ranges]
    chosen: Dict[str, Symbol] = {}
    for spec in specs:
        path_text, _, symbol_text = spec.partition("::")
        infos = project.find_modules(path_text)
        if not infos:
            raise SelectionError(
                f"'{path_text}' is not a Python file, package or module inside {project.root}"
            )
        wanted = list(names)
        if symbol_text:
            wanted.append(symbol_text.replace("::", "."))
        if wanted:
            for name in wanted:
                found = [s for info in infos for s in info.members() if _matches(s, name)]
                if not found:
                    known = sorted({s.local for info in infos for s in info.members()})
                    close = difflib.get_close_matches(name, known, n=3)
                    hint = f" (did you mean: {', '.join(close)}?)" if close else ""
                    raise SelectionError(f"no symbol '{name}' in {path_text}{hint}")
                for symbol in found:
                    chosen[symbol.qualname] = symbol
        elif spans:
            for info in infos:
                for start, end in spans:
                    for symbol in symbols_for_lines(info, range(start, end + 1)):
                        chosen[symbol.qualname] = symbol
        else:
            for info in infos:
                members = info.members() or [info.module_symbol]
                for symbol in members:
                    chosen[symbol.qualname] = symbol
    return sorted(chosen.values(), key=lambda s: (s.path, s.lineno, s.qualname))


def changes_from_git(
    project: Project,
    ref: Optional[str] = None,
    staged: bool = False,
    only: Optional[Set[str]] = None,
) -> Changes:
    top = gitdiff.toplevel(project.root)
    result = Changes()
    for file_diff in gitdiff.diff(project.root, ref, staged):
        new_abs = (top / file_diff.new_path).resolve() if file_diff.new_path else None
        old_abs = (top / file_diff.old_path).resolve() if file_diff.old_path else None
        anchor = new_abs or old_abs
        if anchor is None or not _inside(anchor, project.root):
            continue
        if only is not None and str(anchor) not in only:
            continue
        new_info = project.by_path.get(str(new_abs)) if new_abs else None
        if new_abs is not None and new_info is None and new_abs.exists():
            continue
        old_info = None
        if file_diff.old_path and old_abs is not None:
            if ref:
                spec = f"{ref}:{file_diff.old_path}"
            elif staged:
                spec = f"HEAD:{file_diff.old_path}"
            else:
                spec = f":{file_diff.old_path}"
            source = gitdiff.show(top, spec)
            if source is not None:
                if new_info is not None and new_abs == old_abs:
                    name = new_info.name
                else:
                    name = module_name(old_abs, project.root)
                old_info = parse_source(
                    source,
                    name,
                    str(old_abs),
                    old_abs.relative_to(project.root).as_posix(),
                    is_package=old_abs.name == "__init__.py",
                    is_test=project.is_test_path(old_abs),
                    is_conftest=old_abs.name == "conftest.py",
                )
        result.files.append(anchor.relative_to(project.root).as_posix())
        _merge_file(file_diff, new_info, old_info, result)
    if not staged:
        for path in gitdiff.untracked(project.root):
            absolute = (top / path).resolve()
            if only is not None and str(absolute) not in only:
                continue
            info = project.by_path.get(str(absolute))
            if info is None:
                continue
            result.files.append(info.rel)
            for symbol in info.members():
                result.added[symbol.qualname] = symbol
    return result


def _inside(path: Path, root: Path) -> bool:
    return path == root or root in path.parents


def _merge_file(
    file_diff: gitdiff.FileDiff,
    new_info: Optional[ModuleInfo],
    old_info: Optional[ModuleInfo],
    result: Changes,
) -> None:
    if new_info is None:
        if old_info is not None:
            for qualname, symbol in old_info.symbols.items():
                result.removed[qualname] = symbol
        return
    if old_info is None:
        for symbol in new_info.members():
            result.added[symbol.qualname] = symbol
        return
    new_symbols = new_info.symbols
    old_symbols = old_info.symbols
    for qualname, symbol in old_symbols.items():
        if qualname not in new_symbols and symbol.kind != MODULE:
            result.removed[qualname] = symbol
    for qualname, symbol in new_symbols.items():
        if qualname not in old_symbols and symbol.kind != MODULE:
            result.added[qualname] = symbol
    for hunk in file_diff.hunks:
        if not hunk.significant:
            continue
        for symbol in symbols_for_lines(new_info, hunk.new_lines()):
            if symbol.qualname not in result.added:
                result.changed[symbol.qualname] = symbol
        for symbol in symbols_for_lines(old_info, hunk.old_lines()):
            qualname = symbol.qualname
            if qualname in new_symbols and qualname not in result.added:
                result.changed[qualname] = new_symbols[qualname]


Neighbors = Callable[[str], List[Edge]]


def _bfs(neighbors: Neighbors, starts: Sequence[str], max_depth: int, likely: bool) -> Dict[str, Tuple[int, str, Optional[int]]]:
    hits: Dict[str, Tuple[int, str, Optional[int]]] = {}
    seen: Set[str] = set(starts)
    queue = deque((start, 0) for start in starts)
    while queue:
        node, depth = queue.popleft()
        if max_depth and depth >= max_depth:
            continue
        for edge in sorted(neighbors(node), key=lambda e: e.target):
            if not likely and edge.confidence != EXACT:
                continue
            if edge.target in seen:
                continue
            seen.add(edge.target)
            hits[edge.target] = (depth + 1, node, edge.lineno)
            queue.append((edge.target, depth + 1))
    return hits


def walk(neighbors: Neighbors, starts: Sequence[str], max_depth: int = 0) -> Dict[str, Tuple[int, str, Optional[int], str]]:
    exact = _bfs(neighbors, starts, max_depth, False)
    full = _bfs(neighbors, starts, max_depth, True)
    merged: Dict[str, Tuple[int, str, Optional[int], str]] = {}
    for qualname, data in full.items():
        if qualname in exact:
            depth, via, line = exact[qualname]
            merged[qualname] = (depth, via, line, EXACT)
        else:
            depth, via, line = data
            merged[qualname] = (depth, via, line, LIKELY)
    return merged


def _removed_overlay(project: Project, removed: Sequence[Symbol]) -> Dict[str, List[Edge]]:
    overlay: Dict[str, List[Edge]] = defaultdict(list)
    for symbol in removed:
        prefix = symbol.qualname + "."
        for expanded, users in project.unresolved.items():
            if expanded == symbol.qualname or expanded.startswith(prefix):
                for scope, line in users:
                    overlay[symbol.qualname].append(Edge(scope, EXACT, line))
    return overlay


def _imports_module(info: ModuleInfo, module: str) -> bool:
    prefix = module + "."
    return any(imported == module or imported.startswith(prefix) for imported in info.imported_modules)


def analyze(
    project: Project,
    changed: Sequence[Symbol],
    removed: Sequence[Symbol] = (),
    added: Sequence[Symbol] = (),
    depth: int = 0,
    title: str = "",
    mode: str = IMPACT,
    warnings: Sequence[str] = (),
) -> Report:
    started = perf_counter()
    graph = project.graph
    starts = [s.qualname for s in changed] + [s.qualname for s in removed]
    overlay = _removed_overlay(project, removed) if mode == IMPACT else {}

    if mode == IMPACT:
        def neighbors(node: str) -> List[Edge]:
            return graph.dependents(node) + overlay.get(node, [])
    else:
        def neighbors(node: str) -> List[Edge]:
            return graph.dependencies(node)

    found = walk(neighbors, starts, depth)
    hits: List[Hit] = []
    for qualname, (level, via, line, confidence) in found.items():
        symbol = project.symbol(qualname)
        if symbol is None:
            continue
        nodeid = project.nodeid(symbol) if mode == IMPACT else None
        reference = line if mode == IMPACT else None
        hits.append(Hit(symbol, level, via, confidence, reference, nodeid, _role(project, symbol, nodeid)))
    hits.sort(key=lambda h: (h.depth, h.symbol.qualname))

    tests: Set[str] = set()
    related: List[str] = []
    broken: Set[str] = set()
    if mode == IMPACT:
        tests.update(h.nodeid for h in hits if h.nodeid)
        for symbol in changed:
            nodeid = project.nodeid(symbol)
            if nodeid:
                tests.add(nodeid)
            elif symbol.kind == MODULE and project.modules.get(symbol.module) and project.modules[symbol.module].is_test:
                tests.add(project.modules[symbol.module].rel)
        broken = _broken_imports(project, removed)
        for name in broken:
            info = project.modules[name]
            if info.is_test:
                tests.add(info.rel)
        tests = _collapse(tests)
        covered = {t.split("::", 1)[0] for t in tests}
        touched = {s.module for s in list(changed) + list(removed)}
        related = sorted(
            {
                info.rel
                for info in project.modules.values()
                if info.is_test
                and info.rel not in covered
                and any(_imports_module(info, module) for module in touched)
            }
        )

    nodes = set(starts) | set(found)
    edges: List[Tuple[str, str, str]] = []
    for node in sorted(nodes):
        for edge in graph.dependencies(node):
            if edge.target in nodes:
                edges.append((node, edge.target, edge.confidence))
    for target, overlay_edges in overlay.items():
        for edge in overlay_edges:
            if edge.target in nodes:
                edges.append((edge.target, target, EXACT))

    all_warnings = list(warnings)
    if project.errors:
        sample = ", ".join(sorted(project.errors)[:3])
        more = "" if len(project.errors) <= 3 else f" and {len(project.errors) - 3} more"
        all_warnings.append(f"could not parse {len(project.errors)} file(s): {sample}{more}")

    stats: Dict[str, object] = {
        "files": len(project.modules),
        "symbols": len(project.resolver.symbols),
        "edges": graph.edge_count,
        "elapsed": round(project.elapsed + (perf_counter() - started), 4),
        "depth_limit": depth,
        "fuzzy": project.resolver.fuzzy_limit,
    }
    return Report(
        mode=mode,
        title=title,
        root=str(project.root),
        changed=list(changed),
        removed=list(removed),
        added=list(added),
        hits=hits,
        edges=edges,
        tests=sorted(tests),
        broken=sorted((name, project.modules[name].rel) for name in broken) if mode == IMPACT else [],
        related_tests=related,
        stats=stats,
        warnings=all_warnings,
    )


def _role(project: Project, symbol: Symbol, nodeid: Optional[str]) -> str:
    if nodeid:
        return "test"
    info = project.modules.get(symbol.module)
    if info is not None and symbol.qualname in info.fixtures.values():
        return "fixture"
    if info is not None and (info.is_test or info.is_conftest):
        return "test-support"
    return "code"


def _broken_imports(project: Project, removed: Sequence[Symbol]) -> Set[str]:
    names = {symbol.qualname for symbol in removed}
    if not names:
        return set()

    def gone(target: str) -> bool:
        return any(target == name or target.startswith(name + ".") for name in names)

    broken = {
        info.name
        for info in project.modules.values()
        if any(gone(target) for target in info.imports.values()) or any(gone(m) for m in info.star_imports)
    }
    queue = list(broken)
    while queue:
        module = queue.pop()
        prefix = module + "."
        for info in project.modules.values():
            if info.name in broken:
                continue
            if any(imported == module or imported.startswith(prefix) for imported in info.imported_modules):
                broken.add(info.name)
                queue.append(info.name)
    return broken


def _collapse(tests: Set[str]) -> Set[str]:
    result: Set[str] = set()
    for test in tests:
        parts = test.split("::")
        if any("::".join(parts[:i]) in tests for i in range(1, len(parts))):
            continue
        result.add(test)
    return result
