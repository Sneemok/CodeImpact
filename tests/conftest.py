import shutil
import subprocess
import textwrap
from pathlib import Path
from typing import Callable, Dict

import pytest

from codeimpact.project import Project, Settings

DEMO = Path(__file__).resolve().parent.parent / "examples" / "demo"


def write_tree(root: Path, files: Dict[str, str]) -> None:
    for name, content in files.items():
        target = root / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(textwrap.dedent(content).lstrip("\n"), encoding="utf-8")


@pytest.fixture
def make_project(tmp_path: Path) -> Callable[..., Project]:
    def build(files: Dict[str, str], fuzzy: int = 1, exclude=()) -> Project:
        write_tree(tmp_path, files)
        return Project.load(Settings(root=tmp_path, fuzzy=fuzzy, exclude=exclude, use_cache=False))

    return build


@pytest.fixture
def demo(tmp_path: Path) -> Path:
    target = tmp_path / "demo"
    shutil.copytree(DEMO, target, ignore=shutil.ignore_patterns(".pytest_cache", ".codeimpact_cache", "__pycache__"))
    return target


def git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-c", "user.name=codeimpact", "-c", "user.email=ci@example.com", *args],
        cwd=str(cwd),
        check=True,
        capture_output=True,
        text=True,
    ).stdout


@pytest.fixture
def run_git() -> Callable[..., str]:
    return git


@pytest.fixture
def repo(demo: Path) -> Path:
    if shutil.which("git") is None:
        pytest.skip("git is not installed")
    git(demo, "init", "-q")
    git(demo, "add", "-A")
    git(demo, "commit", "-q", "-m", "init")
    return demo
