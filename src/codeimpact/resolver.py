from __future__ import annotations

import argparse
import collections
import datetime
import io
import logging
import pathlib
import re
import subprocess
import threading
from collections import defaultdict
from typing import Dict, FrozenSet, List, Optional, Sequence, Set, Tuple

from .models import CLASS, EXACT, LIKELY, METHOD, MODULE, ModuleInfo, Reference, Symbol

Target = Tuple[str, str]
Resolution = Tuple[List[Target], Optional[str]]

_MISSING = object()


def _common_attributes() -> FrozenSet[str]:
    names: Set[str] = set()
    kinds = (
        object, dict, list, tuple, set, frozenset, str, bytes, bytearray, int, float, complex,
        pathlib.Path, io.StringIO, io.BytesIO, io.TextIOWrapper, re.Pattern, re.Match,
        datetime.datetime, datetime.timedelta, collections.deque, collections.Counter,
        collections.OrderedDict, logging.Logger, threading.Thread, threading.Event,
        subprocess.Popen, argparse.ArgumentParser,
    )
    for kind in kinds:
        names.update(dir(kind))
    names.update(
        {
            "get", "set", "put", "post", "delete", "patch", "head", "options",
            "run", "start", "stop", "close", "open", "read", "write", "readline",
            "send", "recv", "execute", "executemany", "fetchone", "fetchall", "fetchmany",
            "commit", "rollback", "flush", "connect", "cursor", "call",
            "dump", "dumps", "load", "loads", "json", "text", "content",
            "info", "debug", "warning", "warn", "error", "exception", "critical", "log",
            "wait", "result", "cancel", "done", "submit", "acquire", "release",
            "next", "reset", "clear", "update", "main", "setUp", "tearDown",
        }
    )
    return frozenset(names)


COMMON_ATTRIBUTES = _common_attributes()


class Resolver:
    def __init__(self, modules: Dict[str, ModuleInfo], fuzzy: int = 1) -> None:
        self.modules = modules
        self.fuzzy_limit = max(0, int(fuzzy))
        self.symbols: Dict[str, Symbol] = {}
        for info in modules.values():
            self.symbols.update(info.symbols)
        self.methods: Dict[str, List[str]] = defaultdict(list)
        for qualname, symbol in self.symbols.items():
            if symbol.kind == METHOD and not modules[symbol.module].is_test:
                self.methods[symbol.name].append(qualname)
        self._canonical: Dict[str, object] = {}
        self._mro: Dict[str, List[str]] = {}
        self._active: Set[str] = set()

    def resolve(self, info: ModuleInfo, ref: Reference, scope: str) -> Resolution:
        if ref.absolute:
            dotted = ".".join(ref.chain)
            target = self.canonical(dotted)
            return ([(target, EXACT)] if target else []), dotted
        head, rest = ref.chain[0], ref.chain[1:]
        owner = self.symbols.get(scope)
        cls = owner.parent if owner is not None and owner.kind == METHOD else None
        if cls and head in ("self", "cls"):
            if not rest:
                return [], None
            return self._member(cls, rest, len(rest) > 1)
        if cls and head == "super":
            if not rest:
                return [], None
            for base in self.mro(cls)[1:]:
                found = self.member(base, rest)
                if found:
                    return [(found, EXACT)], None
            return self.fuzzy(rest), None
        types = info.types.get(scope)
        if types and head in types:
            if not rest:
                return [], None
            typed = types[head]
            if cls and typed in (("cls",), ("self",)):
                target = cls
            else:
                target, _ = self.resolve_global(info, typed)
            if target and self.symbols[target].kind == CLASS:
                return self._member(target, rest, len(rest) > 1)
            return self.fuzzy(rest), None
        scope_locals = info.locals.get(scope)
        if scope_locals and head in scope_locals:
            return (self.fuzzy(rest) if rest else []), None
        target, expanded = self.resolve_global(info, ref.chain)
        if target:
            return [(target, EXACT)], expanded
        return [], expanded

    def _member(self, cls: str, rest: Sequence[str], allow_fuzzy: bool) -> Resolution:
        found = self.member(cls, rest)
        if found:
            return [(found, EXACT)], None
        expanded = ".".join((cls, *rest))
        return (self.fuzzy(rest) if allow_fuzzy else []), expanded

    def resolve_global(self, info: ModuleInfo, chain: Sequence[str]) -> Tuple[Optional[str], Optional[str]]:
        head = chain[0]
        base: Optional[str] = None
        local = f"{info.name}.{head}"
        symbol = self.symbols.get(local)
        if symbol is not None and symbol.module == info.name and symbol.kind != MODULE and symbol.parent is None:
            base = local
        elif head in info.imports:
            base = info.imports[head]
        else:
            for star in info.star_imports:
                candidate = f"{star}.{head}"
                if self.canonical(candidate):
                    base = candidate
                    break
        if base is None:
            return None, None
        expanded = ".".join((base, *chain[1:]))
        return self.canonical(expanded), expanded

    def canonical(self, dotted: str) -> Optional[str]:
        cached = self._canonical.get(dotted, _MISSING)
        if cached is not _MISSING:
            return cached
        self._canonical[dotted] = None
        result = self._lookup(dotted)
        self._canonical[dotted] = result
        return result

    def _lookup(self, dotted: str) -> Optional[str]:
        parts = dotted.split(".")
        for index in range(len(parts), 0, -1):
            prefix = ".".join(parts[:index])
            symbol = self.symbols.get(prefix)
            if symbol is None:
                continue
            rest = parts[index:]
            if not rest:
                return prefix
            if symbol.kind == CLASS:
                return self.member(prefix, rest)
            if symbol.kind == MODULE:
                info = self.modules.get(prefix)
                if info is None:
                    return None
                head = rest[0]
                if head in info.imports:
                    return self.canonical(".".join((info.imports[head], *rest[1:])))
                for star in info.star_imports:
                    found = self.canonical(".".join((star, *rest)))
                    if found:
                        return found
                return None
            return prefix
        return None

    def member(self, cls: str, rest: Sequence[str]) -> Optional[str]:
        attribute = rest[0]
        for klass in self.mro(cls):
            candidate = f"{klass}.{attribute}"
            symbol = self.symbols.get(candidate)
            if symbol is not None and symbol.parent == klass:
                if len(rest) > 1:
                    if symbol.kind == CLASS:
                        return self.member(candidate, rest[1:])
                    typed = self.attribute_type(klass, attribute)
                    if typed:
                        return self.member(typed, rest[1:]) or candidate
                return candidate
        if len(rest) > 1:
            for klass in self.mro(cls):
                typed = self.attribute_type(klass, attribute)
                if typed:
                    return self.member(typed, rest[1:])
        return None

    def attribute_type(self, cls: str, attribute: str) -> Optional[str]:
        symbol = self.symbols.get(cls)
        info = self.modules.get(symbol.module) if symbol else None
        if info is None:
            return None
        chain = info.attr_types.get(cls, {}).get(attribute)
        if not chain:
            return None
        target, _ = self.resolve_global(info, chain)
        if target and target != cls and self.symbols[target].kind == CLASS:
            return target
        return None

    def mro(self, cls: str) -> List[str]:
        cached = self._mro.get(cls)
        if cached is not None:
            return cached
        if cls in self._active:
            return [cls]
        self._active.add(cls)
        try:
            order = [cls]
            symbol = self.symbols.get(cls)
            info = self.modules.get(symbol.module) if symbol else None
            if info is not None:
                for chain in info.bases.get(cls, ()):
                    target, _ = self.resolve_global(info, chain)
                    if target and target != cls and self.symbols[target].kind == CLASS:
                        for klass in self.mro(target):
                            if klass not in order:
                                order.append(klass)
            self._mro[cls] = order
            return order
        finally:
            self._active.discard(cls)

    def fuzzy(self, rest: Sequence[str]) -> List[Target]:
        if not self.fuzzy_limit or not rest:
            return []
        name = rest[-1]
        if name.startswith("__") or name in COMMON_ATTRIBUTES:
            return []
        candidates = self.methods.get(name, [])
        if 0 < len(candidates) <= self.fuzzy_limit:
            return [(candidate, LIKELY) for candidate in candidates]
        return []
