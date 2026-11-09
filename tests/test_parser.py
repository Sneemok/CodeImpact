import textwrap

from codeimpact.parser import annotation_chain, parse_source
import ast


def parse(code: str, name: str = "pkg.mod", is_package: bool = False):
    return parse_source(textwrap.dedent(code), name, "/x/mod.py", "mod.py", is_package=is_package)


def test_definitions_and_ranges():
    info = parse(
        """
        import os

        LIMIT = 10

        @decorator
        def top(a, b=1):
            def inner():
                return a
            return inner()

        class Box(Base):
            size: int = 3

            def open(self):
                return self.size
        """
    )
    kinds = {q: s.kind for q, s in info.symbols.items()}
    assert kinds == {
        "pkg.mod": "module",
        "pkg.mod.LIMIT": "variable",
        "pkg.mod.top": "function",
        "pkg.mod.Box": "class",
        "pkg.mod.Box.size": "attribute",
        "pkg.mod.Box.open": "method",
    }
    top = info.symbols["pkg.mod.top"]
    assert (top.lineno, top.end_lineno) == (6, 10)
    assert info.symbols["pkg.mod.Box.open"].parent == "pkg.mod.Box"
    assert info.bases["pkg.mod.Box"] == [("Base",)]


def test_relative_imports_resolve_against_package():
    info = parse(
        """
        from . import sibling
        from .core import engine as eng
        from ..shared.util import helper
        import json as j
        import a.b.c
        """,
        name="app.sub.mod",
    )
    assert info.imports["sibling"] == "app.sub.sibling"
    assert info.imports["eng"] == "app.sub.core.engine"
    assert info.imports["helper"] == "app.shared.util.helper"
    assert info.imports["j"] == "json"
    assert info.imports["a"] == "a"


def test_package_init_relative_import():
    info = parse("from .auth import login\n", name="app", is_package=True)
    assert info.imports["login"] == "app.auth.login"


def test_references_locals_and_types():
    info = parse(
        """
        def run(repo: "Repo", user=None):
            item = Item()
            with Lock() as lock:
                lock.hold()
            repo.save(item)
            helper.go()
        """
    )
    scope = "pkg.mod.run"
    chains = {r.chain for r in info.refs[scope]}
    assert ("repo", "save") in chains
    assert ("helper", "go") in chains
    assert ("Item",) in chains
    assert {"repo", "user", "item", "lock"} <= info.locals[scope]
    assert info.types[scope]["repo"] == ("Repo",)
    assert info.types[scope]["item"] == ("Item",)
    assert info.types[scope]["lock"] == ("Lock",)


def test_fixtures_usefixtures_and_patch_targets():
    info = parse(
        """
        import pytest
        from unittest import mock

        @pytest.fixture(name="db")
        def database():
            return 1

        @pytest.mark.usefixtures("clean")
        def test_it(db):
            with mock.patch("pkg.auth.login"):
                pass
        """,
        name="tests.test_x",
    )
    assert info.fixtures == {"db": "tests.test_x.database"}
    assert info.usefixtures["tests.test_x.test_it"] == ["clean"]
    assert any(r.absolute and r.chain == ("pkg", "auth", "login") for r in info.refs["tests.test_x.test_it"])


def test_annotation_chain_unwraps_optional():
    for source in ("Optional[User]", "User | None", "'models.User'", "Union[None, User]"):
        found = annotation_chain(ast.parse(source, mode="eval").body)
        assert found and found[-1] == "User"
    assert annotation_chain(ast.parse("List[User]", mode="eval").body) is None


def test_syntax_error_is_reported_not_raised():
    info = parse("def broken(:\n    pass\n")
    assert info.error and "SyntaxError" in info.error
    assert list(info.symbols) == ["pkg.mod"]
