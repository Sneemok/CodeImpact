from codeimpact.analysis import analyze, changes_from_git
from codeimpact.gitdiff import normalize, parse
from codeimpact.project import Project, Settings

SAMPLE = """\
diff --git a/src/a.py b/src/a.py
index 1..2 100644
--- a/src/a.py
+++ b/src/a.py
@@ -3,2 +3,3 @@ def f():
-    return 1
-    # note
+    return 2
+    # changed note
+
@@ -10 +11 @@ def g():
-x = 1  # one
+x = 1  # uno
diff --git a/old.py b/old.py
deleted file mode 100644
--- a/old.py
+++ /dev/null
@@ -1 +0,0 @@
-gone = True
"""


def test_parse_hunks_and_significance():
    files = parse(SAMPLE)
    assert [(f.old_path, f.new_path) for f in files] == [("src/a.py", "src/a.py"), ("old.py", None)]
    first, second = files[0].hunks
    assert (first.old_start, first.old_count, first.new_start, first.new_count) == (3, 2, 3, 3)
    assert first.significant
    assert first.new_lines() == [3]
    assert first.old_lines() == [3]
    assert not second.significant


def test_normalize_ignores_comments_and_spacing():
    assert normalize("x=1  # hi") == normalize("x = 1")
    assert normalize("   # only a comment") == ""
    assert normalize('s = "a # b"') == 's = "a # b"'


def load(root):
    return Project.load(Settings(root=root, use_cache=False))


def edit(path, old, new):
    text = path.read_text(encoding="utf-8")
    assert old in text
    path.write_text(text.replace(old, new), encoding="utf-8")


def test_modified_function_is_detected(repo):
    edit(repo / "src" / "storage.py", "return len(self._users)", "return len(self._users) + 0")
    changes = changes_from_git(load(repo))
    assert list(changes.changed) == ["storage.UserStore.count"]
    assert not changes.removed


def test_comment_only_change_is_ignored(repo):
    edit(repo / "src" / "auth.py", "TOKEN_TTL = 3600", "TOKEN_TTL = 3600  # seconds")
    changes = changes_from_git(load(repo))
    assert not changes.changed and not changes.removed


def test_removed_function_reports_broken_callers(repo):
    edit(
        repo / "src" / "admin.py",
        "def reset_password(",
        "def _unused(",
    )
    project = load(repo)
    changes = changes_from_git(project)
    assert "admin.reset_password" in changes.removed
    assert "admin._unused" in changes.added
    report = analyze(project, list(changes.changed.values()), list(changes.removed.values()))
    assert report.removed[0].qualname == "admin.reset_password"


def test_removed_symbol_with_callers(repo):
    edit(
        repo / "src" / "auth.py",
        'def logout(session: Session) -> None:\n    session.created = 0\n',
        "",
    )
    project = load(repo)
    changes = changes_from_git(project)
    report = analyze(project, list(changes.changed.values()), list(changes.removed.values()))
    assert {h.symbol.qualname for h in report.hits} == {"api.handle_logout"}
    assert [name for name, _ in report.broken] == ["api", "test_api"]
    assert "tests/test_api.py" in report.tests
    assert report.risk == "high"


def test_untracked_files_are_added(repo):
    (repo / "src" / "extra.py").write_text("def brand_new():\n    return 1\n", encoding="utf-8")
    changes = changes_from_git(load(repo))
    assert "extra.brand_new" in changes.added
