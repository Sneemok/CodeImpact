from codeimpact.models import EXACT, LIKELY


def deps(project, qualname):
    return {edge.target: edge.confidence for edge in project.graph.dependencies(qualname)}


def test_imports_aliases_and_reexports(make_project):
    project = make_project(
        {
            "app/__init__.py": "from .auth import login\n",
            "app/auth.py": "def login():\n    return 1\n",
            "app/api.py": """
                import app
                import app.auth as a
                from app import login as do_login

                def one():
                    return app.login()

                def two():
                    return a.login()

                def three():
                    return do_login()
            """,
        }
    )
    for name in ("one", "two", "three"):
        assert deps(project, f"app.api.{name}") == {"app.auth.login": EXACT}


def test_self_inheritance_and_super(make_project):
    project = make_project(
        {
            "shapes.py": """
                class Base:
                    def area(self):
                        return 0

                    def describe(self):
                        return self.area()

                class Square(Base):
                    def area(self):
                        return super().area() + 1

                    def report(self):
                        return self.describe()
            """,
        }
    )
    assert deps(project, "shapes.Base.describe") == {"shapes.Base.area": EXACT}
    assert "shapes.Base.area" in deps(project, "shapes.Square.area")
    assert deps(project, "shapes.Square.report") == {"shapes.Base.describe": EXACT}
    assert "shapes.Base" in deps(project, "shapes.Square")


def test_typed_parameters_and_constructor_link(make_project):
    project = make_project(
        {
            "repo.py": """
                class Repo:
                    def __init__(self, url):
                        self.url = url

                    def persist(self, item):
                        return item
            """,
            "service.py": """
                from typing import Optional
                from repo import Repo

                def store(repo: Optional[Repo], item):
                    return repo.persist(item)

                def build():
                    r = Repo("x")
                    return r.persist(1)
            """,
        }
    )
    assert deps(project, "service.store") == {"repo.Repo.persist": EXACT}
    assert deps(project, "service.build") == {"repo.Repo": EXACT, "repo.Repo.persist": EXACT}
    assert deps(project, "repo.Repo") == {"repo.Repo.__init__": EXACT}


def test_fuzzy_unique_method_and_strict_mode(make_project):
    files = {
        "a.py": """
            class Mailer:
                def deliver_mail(self):
                    return 1
        """,
        "b.py": """
            def notify(service):
                service.deliver_mail()
                service.get()
        """,
    }
    assert deps(make_project(files), "b.notify") == {"a.Mailer.deliver_mail": LIKELY}
    assert deps(make_project(files, fuzzy=0), "b.notify") == {}


def test_local_shadowing_and_star_imports(make_project):
    project = make_project(
        {
            "lib.py": "def tool():\n    return 1\n\ndef other():\n    return 2\n",
            "use.py": """
                from lib import *

                def shadow(tool):
                    return tool()

                def star():
                    return other()
            """,
        }
    )
    assert deps(project, "use.shadow") == {}
    assert deps(project, "use.star") == {"lib.other": EXACT}


def test_module_variables_and_class_attributes(make_project):
    project = make_project(
        {
            "conf.py": """
                TIMEOUT = 5

                class Settings:
                    retries = 3

                    def total(self):
                        return self.retries * TIMEOUT
            """,
            "net.py": """
                import conf

                def wait():
                    return conf.TIMEOUT
            """,
        }
    )
    assert deps(project, "net.wait") == {"conf.TIMEOUT": EXACT}
    assert deps(project, "conf.Settings.total") == {"conf.Settings.retries": EXACT, "conf.TIMEOUT": EXACT}


def test_mock_patch_strings_create_edges(make_project):
    project = make_project(
        {
            "svc.py": "def charge():\n    return 1\n",
            "tests/test_svc.py": """
                from unittest import mock

                def test_charge():
                    with mock.patch("svc.charge"):
                        pass
            """,
        }
    )
    assert deps(project, "test_svc.test_charge") == {"svc.charge": EXACT}


def test_pytest_fixtures_link_through_conftest(make_project):
    project = make_project(
        {
            "core.py": "def make():\n    return 1\n",
            "tests/conftest.py": """
                import pytest
                from core import make

                @pytest.fixture
                def thing():
                    return make()
            """,
            "tests/test_core.py": """
                def test_thing(thing):
                    assert thing

                class TestGroup:
                    def test_method(self, thing):
                        assert thing
            """,
        }
    )
    assert deps(project, "test_core.test_thing") == {"conftest.thing": EXACT}
    assert deps(project, "test_core.TestGroup.test_method") == {"conftest.thing": EXACT}
