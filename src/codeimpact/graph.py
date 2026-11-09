from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from .models import ATTRIBUTE, CLASS, EXACT, LIKELY, ModuleInfo, Symbol
from .resolver import Resolver

CONSTRUCTORS = ("__init__", "__new__", "__post_init__")


@dataclass(frozen=True)
class Edge:
    target: str
    confidence: str
    lineno: Optional[int]


class Graph:
    def __init__(self) -> None:
        self.forward: Dict[str, Dict[str, Edge]] = defaultdict(dict)
        self.reverse: Dict[str, Dict[str, Edge]] = defaultdict(dict)

    def add(self, source: str, target: str, confidence: str = EXACT, lineno: Optional[int] = None) -> None:
        if source == target:
            return
        existing = self.forward[source].get(target)
        if existing is not None and (existing.confidence == EXACT or confidence == LIKELY):
            return
        self.forward[source][target] = Edge(target, confidence, lineno)
        self.reverse[target][source] = Edge(source, confidence, lineno)

    def dependents(self, qualname: str) -> List[Edge]:
        return list(self.reverse.get(qualname, {}).values())

    def dependencies(self, qualname: str) -> List[Edge]:
        return list(self.forward.get(qualname, {}).values())

    @property
    def edge_count(self) -> int:
        return sum(len(targets) for targets in self.forward.values())


Unresolved = Dict[str, List[Tuple[str, int]]]


def build_graph(modules: Dict[str, ModuleInfo], resolver: Resolver, root: Path) -> Tuple[Graph, Unresolved]:
    graph = Graph()
    unresolved: Unresolved = defaultdict(list)
    symbols = resolver.symbols
    for info in modules.values():
        for scope, refs in info.refs.items():
            for ref in refs:
                targets, expanded = resolver.resolve(info, ref, scope)
                if targets:
                    for target, confidence in targets:
                        graph.add(scope, target, confidence, ref.lineno)
                elif expanded:
                    unresolved[expanded].append((scope, ref.lineno))
        for qualname, symbol in info.symbols.items():
            if symbol.kind == CLASS:
                for name in CONSTRUCTORS:
                    constructor = f"{qualname}.{name}"
                    if constructor in symbols:
                        graph.add(qualname, constructor, EXACT, symbols[constructor].lineno)
            elif symbol.kind == ATTRIBUTE and symbol.parent:
                graph.add(symbol.parent, qualname, EXACT, symbol.lineno)
    _link_fixtures(modules, resolver, graph, root)
    return graph, dict(unresolved)


def _link_fixtures(modules: Dict[str, ModuleInfo], resolver: Resolver, graph: Graph, root: Path) -> None:
    conftests = {Path(info.path).parent: info for info in modules.values() if info.is_conftest}
    if not conftests and not any(info.fixtures for info in modules.values()):
        return
    for info in modules.values():
        fixture_functions = set(info.fixtures.values())
        if not (info.is_test or info.is_conftest or fixture_functions):
            continue
        chain = _conftest_chain(Path(info.path).parent, conftests, root)
        for qualname, params in info.params.items():
            if not (info.is_test or info.is_conftest or qualname in fixture_functions):
                continue
            symbol = resolver.symbols[qualname]
            names = [p for p in params if p not in ("self", "cls")]
            names.extend(info.usefixtures.get(qualname, []))
            if symbol.parent:
                names.extend(info.usefixtures.get(symbol.parent, []))
            if info.is_test and symbol.name.startswith("test") and qualname not in fixture_functions:
                names.extend(n for n in _autouse(info, symbol, chain, resolver) if n not in names)
            for name in names:
                target = _find_fixture(name, qualname, info, chain)
                if target:
                    graph.add(qualname, target, EXACT, symbol.lineno)


def _autouse(info: ModuleInfo, symbol: Symbol, chain: List[ModuleInfo], resolver: Resolver) -> List[str]:
    names: List[str] = []
    for owner in (info, *chain):
        for name in owner.autouse:
            if name in names:
                continue
            fixture = resolver.symbols.get(owner.fixtures.get(name, ""))
            if fixture is None:
                continue
            if fixture.parent and fixture.parent != symbol.parent:
                continue
            names.append(name)
    return names


def _conftest_chain(directory: Path, conftests: Dict[Path, ModuleInfo], root: Path) -> List[ModuleInfo]:
    chain: List[ModuleInfo] = []
    current = directory
    while True:
        found = conftests.get(current)
        if found is not None:
            chain.append(found)
        if current == root or current.parent == current:
            break
        current = current.parent
    return chain


def _find_fixture(name: str, requester: str, info: ModuleInfo, chain: List[ModuleInfo]) -> Optional[str]:
    local = info.fixtures.get(name)
    if local and local != requester:
        return local
    for conftest in chain:
        found = conftest.fixtures.get(name)
        if found and found != requester:
            return found
    return None
