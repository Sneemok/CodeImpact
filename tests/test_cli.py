import json

import pytest

from codeimpact.cli import main


def run(capsys, *args):
    code = main(list(args))
    captured = capsys.readouterr()
    return code, captured.out, captured.err


def test_analyze_text_on_demo(demo, capsys, monkeypatch):
    monkeypatch.chdir(demo)
    code, out, _ = run(capsys, "analyze", "src/auth.py", "-f", "hash_password", "--no-cache", "--no-color")
    assert code == 0
    assert "auth.hash_password" in out
    assert "admin.create_user" in out
    assert "tests/test_api.py::test_login_ok" in out
    assert "Risk" in out


def test_analyze_json_matches_expectations(demo, capsys):
    code, out, _ = run(capsys, "analyze", str(demo / "src" / "storage.py"), "-f", "find_user", "--root", str(demo), "--format", "json", "--no-cache")
    assert code == 0
    data = json.loads(out)
    impacted = {item["symbol"] for item in data["impacted"]}
    assert {"auth.login", "admin.reset_password", "api.handle_login"} <= impacted
    assert "tests/test_auth.py::TestLogin::test_success" in data["tests"]
    assert data["pytest"].startswith("python -m pytest")


def test_tests_only_and_fail_over(demo, capsys, monkeypatch):
    monkeypatch.chdir(demo)
    code, out, err = run(capsys, "analyze", "src/auth.py::login", "--tests-only", "--no-cache", "--fail-over", "0")
    assert code == 1
    assert out.splitlines() == [
        "tests/test_api.py::test_login_ok",
        "tests/test_api.py::test_login_rejected",
        "tests/test_auth.py::TestLogin::test_success",
        "tests/test_auth.py::TestLogin::test_wrong_password",
    ]
    assert "exceed" in err


@pytest.mark.parametrize("fmt, marker", [("markdown", "## CodeImpact"), ("dot", "digraph codeimpact"), ("mermaid", "flowchart LR")])
def test_other_formats(demo, capsys, monkeypatch, fmt, marker):
    monkeypatch.chdir(demo)
    code, out, _ = run(capsys, "analyze", "src/auth.py", "--format", fmt, "--no-cache")
    assert code == 0
    assert out.startswith(marker)


def test_output_file(demo, capsys, monkeypatch, tmp_path):
    monkeypatch.chdir(demo)
    target = tmp_path / "impact.md"
    code, _, err = run(capsys, "analyze", "src/models.py", "--format", "markdown", "-o", str(target), "--no-cache")
    assert code == 0
    assert target.read_text(encoding="utf-8").startswith("## CodeImpact")
    assert "written" in err


def test_deps_and_symbols(demo, capsys, monkeypatch):
    monkeypatch.chdir(demo)
    code, out, _ = run(capsys, "deps", "src/api.py", "-f", "handle_login", "--no-cache", "--no-color")
    assert code == 0
    assert "storage.UserStore.find_user" in out
    code, out, _ = run(capsys, "symbols", "src/auth.py", "--format", "json", "--no-cache")
    rows = {row["symbol"]: row for row in json.loads(out)}
    assert rows["auth.hash_password"]["fan_in"] >= 3


def test_unknown_target_is_a_clean_error(demo, capsys, monkeypatch):
    monkeypatch.chdir(demo)
    code, _, err = run(capsys, "analyze", "src/nope.py", "--no-cache")
    assert code == 2
    assert "error" in err


def test_cache_roundtrip(demo, capsys, monkeypatch):
    monkeypatch.chdir(demo)
    first = run(capsys, "analyze", "src/auth.py", "--format", "json")[1]
    assert (demo / ".codeimpact_cache" / "modules.pickle").exists()
    second = run(capsys, "analyze", "src/auth.py", "--format", "json")[1]
    strip = lambda text: {k: v for k, v in json.loads(text).items() if k != "stats"}
    assert strip(first) == strip(second)


def test_diff_command(repo, capsys, monkeypatch):
    monkeypatch.chdir(repo)
    path = repo / "src" / "models.py"
    path.write_text(path.read_text(encoding="utf-8").replace('"admin" in self.roles', '"root" in self.roles'), encoding="utf-8")
    code, out, _ = run(capsys, "diff", "--format", "json", "--no-cache")
    assert code == 0
    data = json.loads(out)
    assert [c["symbol"] for c in data["changed"]] == ["models.User.is_admin"]
