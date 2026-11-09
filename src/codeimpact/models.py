from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set, Tuple

EXACT = "exact"
LIKELY = "likely"

MODULE = "module"
CLASS = "class"
FUNCTION = "function"
METHOD = "method"
VARIABLE = "variable"
ATTRIBUTE = "attribute"


@dataclass
class Symbol:
    qualname: str
    module: str
    local: str
    kind: str
    path: str
    lineno: int
    end_lineno: int
    parent: Optional[str] = None

    @property
    def name(self) -> str:
        return (self.local or self.module).rsplit(".", 1)[-1]

    @property
    def location(self) -> str:
        return f"{self.path}:{self.lineno}"

    @property
    def span(self) -> int:
        return self.end_lineno - self.lineno

    def covers(self, line: int) -> bool:
        return self.lineno <= line <= self.end_lineno


@dataclass(frozen=True)
class Reference:
    chain: Tuple[str, ...]
    lineno: int
    absolute: bool = False


@dataclass
class ModuleInfo:
    name: str
    path: str
    rel: str
    is_package: bool = False
    is_test: bool = False
    is_conftest: bool = False
    symbols: Dict[str, Symbol] = field(default_factory=dict)
    imports: Dict[str, str] = field(default_factory=dict)
    star_imports: List[str] = field(default_factory=list)
    imported_modules: List[str] = field(default_factory=list)
    refs: Dict[str, List[Reference]] = field(default_factory=dict)
    locals: Dict[str, Set[str]] = field(default_factory=dict)
    types: Dict[str, Dict[str, Tuple[str, ...]]] = field(default_factory=dict)
    bases: Dict[str, List[Tuple[str, ...]]] = field(default_factory=dict)
    attr_types: Dict[str, Dict[str, Tuple[str, ...]]] = field(default_factory=dict)
    params: Dict[str, List[str]] = field(default_factory=dict)
    fixtures: Dict[str, str] = field(default_factory=dict)
    usefixtures: Dict[str, List[str]] = field(default_factory=dict)
    error: Optional[str] = None

    @property
    def module_symbol(self) -> Symbol:
        return self.symbols[self.name]

    def members(self) -> List[Symbol]:
        return [s for s in self.symbols.values() if s.kind != MODULE]
