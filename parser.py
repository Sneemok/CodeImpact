from __future__ import annotations

import ast
from typing import Dict, List, Optional, Set, Tuple, Union

from .models import (
    ATTRIBUTE,
    CLASS,
    FUNCTION,
    METHOD,
    MODULE,
    VARIABLE,
    ModuleInfo,
    Reference,
    Symbol,
)

FUNCTIONS = (ast.FunctionDef, ast.AsyncFunctionDef)
BLOCKS = tuple(
    getattr(ast, name)
    for name in ("If", "Try", "TryStar", "With", "AsyncWith", "For", "AsyncFor", "While", "Match")
    if hasattr(ast, name)
)
WRAPPERS = frozenset({"Optional", "Annotated", "Final", "ClassVar", "Union", "Type", "type"})

Chain = Tuple[str, ...]


def parse_source(
    source: Union[bytes, str],
    name: str,
    path: str,
    rel: str,
    is_package: bool = False,
    is_test: bool = False,
    is_conftest: bool = False,
) -> ModuleInfo:
    info = ModuleInfo(
        name=name,
        path=path,
        rel=rel,
        is_package=is_package,
        is_test=is_test,
        is_conftest=is_conftest,
    )
    newline = b"\n" if isinstance(source, bytes) else "\n"
    total = source.count(newline) + 1
    info.symbols[name] = Symbol(name, name, "", MODULE, rel, 1, max(1, total))
    try:
        tree = ast.parse(source, filename=path)
    except (SyntaxError, ValueError) as exc:
        info.error = f"{type(exc).__name__}: {exc}"
        return info
    _Builder(info).build(tree)
    return info


def chain_of(node: ast.AST) -> Optional[Chain]:
    parts: List[str] = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        parts.append(node.id)
    elif (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "super"
        and parts
    ):
        parts.append("super")
    else:
        return None
    return tuple(reversed(parts))


def annotation_chain(node: Optional[ast.AST]) -> Optional[Chain]:
    if node is None:
        return None
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        try:
            node = ast.parse(node.value, mode="eval").body
        except SyntaxError:
            return None
    if isinstance(node, (ast.Name, ast.Attribute)):
        return chain_of(node)
    if isinstance(node, ast.Subscript):
        base = chain_of(node.value)
        if not base or base[-1] not in WRAPPERS:
            return None
        inner = node.slice
        if isinstance(inner, ast.Tuple):
            for element in inner.elts:
                if not _is_none(element):
                    return annotation_chain(element)
            return None
        return annotation_chain(inner)
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.BitOr):
        for side in (node.left, node.right):
            if not _is_none(side):
                found = annotation_chain(side)
                if found:
                    return found
    return None


def _is_none(node: ast.AST) -> bool:
    return isinstance(node, ast.Constant) and node.value is None


def _target_names(node: ast.AST) -> List[str]:
    if isinstance(node, ast.Name):
        return [node.id]
    if isinstance(node, (ast.Tuple, ast.List)):
        names: List[str] = []
        for element in node.elts:
            names.extend(_target_names(element))
        return names
    if isinstance(node, ast.Starred):
        return _target_names(node.value)
    return []


def _start_line(node: ast.AST) -> int:
    decorators = getattr(node, "decorator_list", [])
    return min([node.lineno] + [d.lineno for d in decorators])


class _Builder:
    def __init__(self, info: ModuleInfo) -> None:
        self.info = info

    def build(self, tree: ast.Module) -> None:
        self.block(tree.body, self.info.name, None, "")

    def block(self, body: List[ast.stmt], owner: str, cls: Optional[str], prefix: str) -> None:
        for node in body:
            self.statement(node, owner, cls, prefix)

    def statement(self, node: ast.stmt, owner: str, cls: Optional[str], prefix: str) -> None:
        if isinstance(node, FUNCTIONS):
            self.function(node, cls, prefix)
        elif isinstance(node, ast.ClassDef):
            self.klass(node, cls, prefix)
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            self.imports(node)
        elif BLOCKS and isinstance(node, BLOCKS):
            self.compound(node, owner, cls, prefix)
        else:
            created = self.variables(node, cls, prefix)
            if created:
                value = getattr(node, "value", None)
                for qualname in created:
                    if value is not None:
                        self.collect(qualname, value)
            else:
                self.collect(owner, node)

    def compound(self, node: ast.stmt, owner: str, cls: Optional[str], prefix: str) -> None:
        for field_name, value in ast.iter_fields(node):
            if field_name == "handlers":
                for handler in value:
                    if handler.type is not None:
                        self.collect(owner, handler.type)
                    self.block(handler.body, owner, cls, prefix)
            elif isinstance(value, list) and value and all(isinstance(v, ast.stmt) for v in value):
                self.block(value, owner, cls, prefix)
            elif isinstance(value, ast.AST):
                self.collect(owner, value)
            elif isinstance(value, list):
                for item in value:
                    if isinstance(item, ast.AST):
                        self.collect(owner, item)

    def variables(self, node: ast.stmt, cls: Optional[str], prefix: str) -> List[str]:
        names: List[str] = []
        if isinstance(node, ast.Assign):
            for target in node.targets:
                names.extend(_target_names(target))
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            names.append(node.target.id)
        if cls and isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            found = annotation_chain(node.annotation)
            if found:
                self.info.attr_types.setdefault(cls, {})[node.target.id] = found
        created: List[str] = []
        for name in names:
            local = prefix + name
            qualname = f"{self.info.name}.{local}"
            existing = self.info.symbols.get(qualname)
            if existing is None:
                self.info.symbols[qualname] = Symbol(
                    qualname,
                    self.info.name,
                    local,
                    ATTRIBUTE if cls else VARIABLE,
                    self.info.rel,
                    node.lineno,
                    node.end_lineno or node.lineno,
                    cls,
                )
                created.append(qualname)
            elif existing.kind in (VARIABLE, ATTRIBUTE):
                created.append(qualname)
        return created

    def function(self, node: ast.AST, cls: Optional[str], prefix: str) -> None:
        local = prefix + node.name
        qualname = f"{self.info.name}.{local}"
        start = _start_line(node)
        end = node.end_lineno or node.lineno
        existing = self.info.symbols.get(qualname)
        if existing is not None and existing.kind in (FUNCTION, METHOD):
            existing.lineno = min(existing.lineno, start)
            existing.end_lineno = max(existing.end_lineno, end)
        else:
            self.info.symbols[qualname] = Symbol(
                qualname,
                self.info.name,
                local,
                METHOD if cls else FUNCTION,
                self.info.rel,
                start,
                end,
                cls,
            )
        arguments = node.args
        every = [*arguments.posonlyargs, *arguments.args, *arguments.kwonlyargs]
        if arguments.vararg:
            every.append(arguments.vararg)
        if arguments.kwarg:
            every.append(arguments.kwarg)
        params = self.info.params.setdefault(qualname, [])
        params.extend(a.arg for a in every if a.arg not in params)
        scope_locals = self.info.locals.setdefault(qualname, set())
        scope_locals.update(a.arg for a in every)
        types = self.info.types.setdefault(qualname, {})
        for argument in every:
            found = annotation_chain(argument.annotation)
            if found:
                types[argument.arg] = found
        self.scan_body(node, scope_locals, types)
        if cls:
            self.instance_attributes(node, cls, types)
        for decorator in node.decorator_list:
            self.decorator(qualname, decorator, node.name)
        self.collect(qualname, *arguments.defaults, *[d for d in arguments.kw_defaults if d is not None])
        self.collect(qualname, *node.body)

    def scan_body(self, node: ast.AST, scope_locals: Set[str], types: Dict[str, Chain]) -> None:
        declared: Set[str] = set()
        for statement in node.body:
            for child in ast.walk(statement):
                if isinstance(child, ast.Name) and isinstance(child.ctx, (ast.Store, ast.Del)):
                    scope_locals.add(child.id)
                elif isinstance(child, ast.arg):
                    scope_locals.add(child.arg)
                elif isinstance(child, (ast.Global, ast.Nonlocal)):
                    declared.update(child.names)
                elif isinstance(child, FUNCTIONS) or isinstance(child, ast.ClassDef):
                    scope_locals.add(child.name)
                elif isinstance(child, ast.ExceptHandler) and child.name:
                    scope_locals.add(child.name)
                elif isinstance(child, (ast.Import, ast.ImportFrom)):
                    self.imports(child)
                elif isinstance(child, ast.Assign):
                    if (
                        len(child.targets) == 1
                        and isinstance(child.targets[0], ast.Name)
                        and isinstance(child.value, ast.Call)
                    ):
                        found = chain_of(child.value.func)
                        if found:
                            types.setdefault(child.targets[0].id, found)
                elif isinstance(child, ast.AnnAssign) and isinstance(child.target, ast.Name):
                    found = annotation_chain(child.annotation)
                    if found:
                        types[child.target.id] = found
                elif isinstance(child, ast.withitem):
                    if isinstance(child.optional_vars, ast.Name) and isinstance(child.context_expr, ast.Call):
                        found = chain_of(child.context_expr.func)
                        if found:
                            types.setdefault(child.optional_vars.id, found)
        scope_locals.difference_update(declared)
        for name in declared:
            types.pop(name, None)

    def instance_attributes(self, node: ast.AST, cls: str, types: Dict[str, Chain]) -> None:
        known = self.info.attr_types.setdefault(cls, {})
        for statement in node.body:
            for child in ast.walk(statement):
                if isinstance(child, ast.Assign):
                    targets, value, annotation = child.targets, child.value, None
                elif isinstance(child, ast.AnnAssign):
                    targets, value, annotation = [child.target], child.value, child.annotation
                else:
                    continue
                for target in targets:
                    if not (
                        isinstance(target, ast.Attribute)
                        and isinstance(target.value, ast.Name)
                        and target.value.id in ("self", "cls")
                    ):
                        continue
                    found = annotation_chain(annotation)
                    if found is None and isinstance(value, ast.Call):
                        found = chain_of(value.func)
                    if found is None and isinstance(value, ast.Name):
                        found = types.get(value.id)
                    if found:
                        known.setdefault(target.attr, found)

    def decorator(self, qualname: str, node: ast.expr, default_name: str) -> None:
        target = node.func if isinstance(node, ast.Call) else node
        found = chain_of(target)
        if found and found[-1] == "fixture":
            fixture_name = default_name
            autouse = False
            if isinstance(node, ast.Call):
                for keyword in node.keywords:
                    if not isinstance(keyword.value, ast.Constant):
                        continue
                    if keyword.arg == "name" and isinstance(keyword.value.value, str):
                        fixture_name = keyword.value.value
                    elif keyword.arg == "autouse" and keyword.value.value is True:
                        autouse = True
            self.info.fixtures[fixture_name] = qualname
            if autouse and fixture_name not in self.info.autouse:
                self.info.autouse.append(fixture_name)
        elif found and found[-1] == "usefixtures" and isinstance(node, ast.Call):
            names = self.info.usefixtures.setdefault(qualname, [])
            for argument in node.args:
                if isinstance(argument, ast.Constant) and isinstance(argument.value, str):
                    names.append(argument.value)
        self.collect(qualname, node)

    def klass(self, node: ast.ClassDef, cls: Optional[str], prefix: str) -> None:
        local = prefix + node.name
        qualname = f"{self.info.name}.{local}"
        self.info.symbols[qualname] = Symbol(
            qualname,
            self.info.name,
            local,
            CLASS,
            self.info.rel,
            _start_line(node),
            node.end_lineno or node.lineno,
            cls,
        )
        bases: List[Chain] = []
        for base in node.bases:
            target = base.value if isinstance(base, ast.Subscript) else base
            found = chain_of(target)
            if found:
                bases.append(found)
        self.info.bases[qualname] = bases
        for decorator in node.decorator_list:
            self.decorator(qualname, decorator, node.name)
        self.collect(qualname, *node.bases, *[k.value for k in node.keywords])
        self.block(node.body, qualname, qualname, local + ".")

    def imports(self, node: ast.stmt) -> None:
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.asname:
                    self.info.imports[alias.asname] = alias.name
                else:
                    head = alias.name.split(".")[0]
                    self.info.imports.setdefault(head, head)
                self.info.imported_modules.append(alias.name)
            return
        base = self.relative(node.module, node.level)
        if base is None:
            return
        if base:
            self.info.imported_modules.append(base)
        for alias in node.names:
            if alias.name == "*":
                self.info.star_imports.append(base)
                continue
            target = f"{base}.{alias.name}" if base else alias.name
            self.info.imports[alias.asname or alias.name] = target
            self.info.imported_modules.append(target)

    def relative(self, module: Optional[str], level: int) -> Optional[str]:
        if not level:
            return module or ""
        package = self.info.name if self.info.is_package else self.info.name.rpartition(".")[0]
        parts = package.split(".") if package else []
        if level - 1 > len(parts):
            return None
        if level > 1:
            parts = parts[: len(parts) - (level - 1)]
        base = ".".join(parts)
        if module:
            base = f"{base}.{module}" if base else module
        return base

    def collect(self, owner: str, *nodes: ast.AST) -> None:
        refs = self.info.refs.setdefault(owner, [])
        stack = [n for n in nodes if n is not None]
        while stack:
            node = stack.pop()
            if isinstance(node, (ast.Name, ast.Attribute)):
                found = chain_of(node)
                if found is not None:
                    if isinstance(node.ctx, ast.Load):
                        refs.append(Reference(found, node.lineno))
                    continue
            if isinstance(node, ast.Call):
                self.patch_target(node, refs)
            stack.extend(ast.iter_child_nodes(node))

    def patch_target(self, node: ast.Call, refs: List[Reference]) -> None:
        found = chain_of(node.func)
        if not found or found[-1] != "patch" or not node.args:
            return
        first = node.args[0]
        if not (isinstance(first, ast.Constant) and isinstance(first.value, str)):
            return
        parts = tuple(first.value.split("."))
        if len(parts) > 1 and all(p.isidentifier() for p in parts):
            refs.append(Reference(parts, node.lineno, True))
