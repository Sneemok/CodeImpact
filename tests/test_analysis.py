import pytest

from codeimpact.analysis import SelectionError, analyze, parse_range, select, symbols_for_lines
from codeimpact.models import LIKELY

CHAIN = {
    "core.py": """
        def base():
            return 1

        def untouched():
            return 2
    """,
    "middle.py": """
        from core import base

        def mid():
            return base()
    """,
    "top.py": """
        from middle import mid

        def top():
            return mid()
    """,
    "tests/test_top.py": """
        from top import top

        def test_top():
            assert top() == 1

        def helper():
            return top()
    """,
}


def test_transitive_impact_and_tests(make_project):
    project = make_project(CHAIN)
    report = analyze(project, select(project, ["core.py"], ["base"]))
    depths = {h.symbol.qualname: h.depth for h in report.hits}
    assert depths == {"middle.mid": 1, "top.top": 2, "test_top.test_top": 3, "test_top.helper": 3}
    assert report.tests == ["tests/test_top.py::test_top"]
    assert [m for m, _, _ in report.modules] == ["middle", "top"]
    assert report.risk == "low"


def test_depth_limit(make_project):
    project = make_project(CHAIN)
    report = analyze(project, select(project, ["core.py::base"]), depth=1)
    assert [h.symbol.qualname for h in report.hits] == ["middle.mid"]
    assert report.tests == []
    assert report.related_tests == []


def test_untouched_symbol_has_no_impact(make_project):
    project = make_project(CHAIN)
    report = analyze(project, select(project, ["core.py"], ["untouched"]))
    assert report.hits == []
    assert report.risk == "none"


def test_whole_file_and_directory_selection(make_project):
    project = make_project(CHAIN)
    names = [s.qualname for s in select(project, ["core.py"])]
    assert names == ["core.base", "core.untouched"]
    assert {s.module for s in select(project, ["."])} >= {"core", "middle", "top"}
    assert [s.qualname for s in select(project, ["middle"])] == ["middle.mid"]


def test_line_selection_picks_innermost(make_project):
    project = make_project(
        {
            "m.py": """
                class A:
                    x = 1

                    def f(self):
                        return 1
            """,
        }
    )
    info = project.modules["m"]
    assert [s.qualname for s in symbols_for_lines(info, [5])] == ["m.A.f"]
    assert [s.qualname for s in symbols_for_lines(info, [2])] == ["m.A.x"]
    assert [s.qualname for s in select(project, ["m.py"], ranges=["4-5"])] == ["m.A.f"]


def test_selection_errors(make_project):
    project = make_project(CHAIN)
    with pytest.raises(SelectionError, match="did you mean: base"):
        select(project, ["core.py"], ["bse"])
    with pytest.raises(SelectionError):
        select(project, ["missing.py"])
    with pytest.raises(SelectionError):
        parse_range("9-3")
    assert parse_range("7") == (7, 7)


def test_likely_confidence_propagates(make_project):
    project = make_project(
        {
            "mail.py": """
                class Mailer:
                    def deliver_mail(self):
                        return 1
            """,
            "jobs.py": """
                def notify(service):
                    return service.deliver_mail()

                def nightly(service):
                    return notify(service)
            """,
        }
    )
    report = analyze(project, select(project, ["mail.py::Mailer.deliver_mail"]))
    assert {h.symbol.qualname: h.confidence for h in report.hits} == {
        "jobs.notify": LIKELY,
        "jobs.nightly": LIKELY,
    }


def test_test_class_collapses_methods(make_project):
    project = make_project(
        {
            "lib.py": "def f():\n    return 1\n",
            "tests/test_lib.py": """
                from lib import f

                class TestF:
                    def test_a(self):
                        assert f()
            """,
        }
    )
    report = analyze(project, select(project, ["tests/test_lib.py"]))
    assert report.tests == ["tests/test_lib.py::TestF"]


def test_autouse_fixture_selects_tests_in_its_scope(make_project):
    project = make_project(
        {
            "lib.py": "def setup_db():\n    return 1\n",
            "tests/conftest.py": """
                import pytest
                from lib import setup_db

                @pytest.fixture(autouse=True)
                def db():
                    return setup_db()
            """,
            "tests/test_a.py": """
                import pytest

                def test_plain():
                    assert True

                class TestGroup:
                    @pytest.fixture(autouse=True)
                    def local(self):
                        return 1

                    def test_inside(self):
                        assert True
            """,
            "other/test_b.py": """
                def test_outside():
                    assert True
            """,
        }
    )
    report = analyze(project, select(project, ["lib.py"], ["setup_db"]))
    assert report.tests == ["tests/test_a.py::TestGroup::test_inside", "tests/test_a.py::test_plain"]
    report = analyze(project, select(project, ["tests/test_a.py::TestGroup.local"]))
    assert report.tests == ["tests/test_a.py::TestGroup::test_inside"]


def test_autouse_fixture_can_be_overridden(make_project):
    project = make_project(
        {
            "lib.py": "def setup_db():\n    return 1\n",
            "tests/conftest.py": """
                import pytest
                from lib import setup_db

                @pytest.fixture(autouse=True)
                def db():
                    return setup_db()
            """,
            "tests/test_a.py": """
                import pytest

                @pytest.fixture
                def db():
                    return None

                def test_plain():
                    assert True
            """,
        }
    )
    report = analyze(project, select(project, ["lib.py"], ["setup_db"]))
    assert report.tests == []


def test_unittest_testcase_is_collected_without_test_prefix(make_project):
    project = make_project(
        {
            "lib.py": "def helper():\n    return 2\n",
            "tests/base.py": """
                import unittest

                class Base(unittest.TestCase):
                    pass
            """,
            "tests/test_lib.py": """
                from unittest import TestCase
                from lib import helper
                from base import Base

                class HelperTests(TestCase):
                    def test_helper(self):
                        assert helper() == 2

                class InheritedTests(Base):
                    def test_again(self):
                        assert helper() == 2

                class Plain:
                    def test_ignored(self):
                        assert helper() == 2
            """,
        }
    )
    report = analyze(project, select(project, ["lib.py"], ["helper"]))
    assert report.tests == [
        "tests/test_lib.py::HelperTests::test_helper",
        "tests/test_lib.py::InheritedTests::test_again",
    ]
