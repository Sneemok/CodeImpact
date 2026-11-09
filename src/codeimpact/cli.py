from __future__ import annotations

import argparse
import json
import os
import shlex
import subprocess
import sys
from pathlib import Path
from typing import List, Optional, Sequence, Tuple

from . import __version__
from .analysis import DEPS, IMPACT, Report, SelectionError, analyze, changes_from_git, select
from .config import ConfigError, find_root, read_config
from .gitdiff import GitError, merge_base
from .project import DEFAULT_TEST_PATTERNS, Project, Settings
from .report import BOLD, DIM, FORMATS, Style, render

EXAMPLES = """\
examples:
  codeimpact analyze src/auth.py
  codeimpact analyze src/auth.py -f login
  codeimpact analyze src/auth.py::Session.refresh --depth 2
  codeimpact analyze src/auth.py --lines 40-58
  codeimpact analyze src/auth.py --since main
  codeimpact diff                      uncommitted changes
  codeimpact diff origin/main --merge-base   only what your branch changed
  codeimpact diff origin/main --format markdown -o impact.md
  codeimpact diff --staged --run       run only the affected tests
  pytest $(codeimpact analyze src/auth.py --tests-only)
  codeimpact deps src/api.py -f handle_login
  codeimpact symbols src/auth.py
"""


def _common() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(add_help=False)
    project = parser.add_argument_group("project")
    project.add_argument("--root", metavar="DIR", help="project root (default: nearest dir with pyproject.toml/.git)")
    project.add_argument("--exclude", metavar="GLOB", action="append", default=[], help="exclude paths, repeatable")
    project.add_argument("--no-cache", action="store_true", help="do not read or write .codeimpact_cache")
    analysis = parser.add_argument_group("analysis")
    analysis.add_argument("-d", "--depth", type=int, metavar="N", help="max propagation depth, 0 = unlimited")
    analysis.add_argument("--fuzzy", type=int, metavar="N", help="link untyped method calls when at most N candidates exist (default 1)")
    analysis.add_argument("--strict", action="store_true", help="only exact resolutions, same as --fuzzy 0")
    output = parser.add_argument_group("output")
    output.add_argument("--format", choices=FORMATS, default="text")
    output.add_argument("-o", "--output", metavar="FILE", help="write the report to a file")
    output.add_argument("--no-color", action="store_true")
    return parser


def _testing() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(add_help=False)
    group = parser.add_argument_group("tests")
    group.add_argument("--tests-only", action="store_true", help="print only pytest node ids, one per line")
    group.add_argument("--run", action="store_true", help="run the selected tests with pytest")
    group.add_argument("--pytest-args", default="", metavar="ARGS", help="extra arguments passed to pytest")
    group.add_argument("--fail-over", type=int, metavar="N", help="exit with 1 when more than N symbols are impacted")
    return parser


def build_parser() -> argparse.ArgumentParser:
    common = _common()
    testing = _testing()
    parser = argparse.ArgumentParser(
        prog="codeimpact",
        description="Change impact analysis for Python: who depends on the code you touched and which tests to run.",
        epilog=EXAMPLES,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    commands = parser.add_subparsers(dest="command", metavar="COMMAND")

    analyze_parser = commands.add_parser(
        "analyze",
        parents=[common, testing],
        help="impact of changing a file, a symbol or a line range",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=EXAMPLES,
    )
    analyze_parser.add_argument("targets", nargs="+", metavar="TARGET", help="file, directory, module or file::Symbol")
    analyze_parser.add_argument("-f", "--function", action="append", default=[], metavar="NAME", help="only this function/class/method, repeatable")
    analyze_parser.add_argument("-l", "--lines", action="append", default=[], metavar="A-B", help="only symbols touching these lines, repeatable")
    analyze_parser.add_argument("--since", metavar="REF", help="only symbols changed in the target since a git ref")
    analyze_parser.add_argument("--merge-base", action="store_true", help="compare with the merge base of --since and HEAD")
    analyze_parser.set_defaults(handler=_cmd_analyze)

    diff_parser = commands.add_parser("diff", parents=[common, testing], help="impact of the current git diff")
    diff_parser.add_argument("ref", nargs="?", help="compare the working tree with this ref (default: index)")
    diff_parser.add_argument("--staged", action="store_true", help="use staged changes")
    diff_parser.add_argument("--merge-base", action="store_true", help="compare with the merge base of REF and HEAD, i.e. only what your branch changed")
    diff_parser.set_defaults(handler=_cmd_diff)

    deps_parser = commands.add_parser("deps", parents=[common], help="what a symbol depends on")
    deps_parser.add_argument("targets", nargs="+", metavar="TARGET")
    deps_parser.add_argument("-f", "--function", action="append", default=[], metavar="NAME")
    deps_parser.set_defaults(handler=_cmd_deps)

    symbols_parser = commands.add_parser("symbols", parents=[common], help="list symbols with fan-in and fan-out")
    symbols_parser.add_argument("targets", nargs="+", metavar="TARGET")
    symbols_parser.set_defaults(handler=_cmd_symbols)
    return parser


def _load(args: argparse.Namespace) -> Tuple[Project, int]:
    root = Path(args.root).expanduser().resolve() if args.root else find_root(Path.cwd())
    if not root.is_dir():
        raise SelectionError(f"root '{root}' is not a directory")
    config = read_config(root)
    exclude = [str(p) for p in config.get("exclude", [])] + list(args.exclude)
    patterns = config.get("test-patterns") or list(DEFAULT_TEST_PATTERNS)
    if args.strict:
        fuzzy = 0
    elif args.fuzzy is not None:
        fuzzy = args.fuzzy
    else:
        fuzzy = int(config.get("fuzzy", 1))
    depth = args.depth if args.depth is not None else int(config.get("depth", 0))
    settings = Settings(root=root, exclude=exclude, test_patterns=patterns, fuzzy=fuzzy, use_cache=not args.no_cache)
    return Project.load(settings), max(0, depth)


def _use_color(args: argparse.Namespace) -> bool:
    if args.format != "text" or args.no_color or args.output:
        return False
    if os.environ.get("NO_COLOR"):
        return False
    if os.environ.get("FORCE_COLOR"):
        return True
    return sys.stdout.isatty()


def _write(text: str, destination: Optional[str]) -> None:
    if destination:
        Path(destination).write_text(text, encoding="utf-8")
        return
    sys.stdout.write(text)
    sys.stdout.flush()


def _finish(report: Report, project: Project, args: argparse.Namespace) -> int:
    if getattr(args, "tests_only", False):
        text = "".join(f"{test}\n" for test in report.tests)
    else:
        text = render(report, args.format, _use_color(args))
    _write(text, args.output)
    if args.output and not getattr(args, "tests_only", False):
        print(f"codeimpact: report written to {args.output}", file=sys.stderr)
    code = 0
    fail_over = getattr(args, "fail_over", None)
    if fail_over is not None and len(report.impacted) > fail_over:
        print(
            f"codeimpact: {len(report.impacted)} impacted symbols exceed the limit of {fail_over}",
            file=sys.stderr,
        )
        code = 1
    if getattr(args, "run", False):
        if not report.tests:
            print("codeimpact: no tests selected, nothing to run", file=sys.stderr)
        else:
            command = [sys.executable, "-m", "pytest", *report.tests, *shlex.split(args.pytest_args)]
            print("codeimpact: " + " ".join(shlex.quote(c) for c in command[1:]), file=sys.stderr)
            result = subprocess.call(command, cwd=str(project.root))
            code = code or result
    return code


def _base(project: Project, ref: Optional[str], use_merge_base: bool, option: str) -> Tuple[Optional[str], str]:
    if not use_merge_base:
        return ref, ref or ""
    if not ref:
        raise SelectionError(f"--merge-base needs {option}, e.g. origin/main")
    base = merge_base(project.root, ref)
    return base, f"{ref} (merge base {base[:10]})"


def _cmd_analyze(args: argparse.Namespace) -> int:
    project, depth = _load(args)
    title = " ".join(args.targets)
    warnings: List[str] = []
    if args.merge_base and not args.since:
        raise SelectionError("--merge-base needs --since REF")
    if args.since:
        files = set()
        for spec in args.targets:
            infos = project.find_modules(spec.partition("::")[0])
            if not infos:
                raise SelectionError(f"'{spec}' is not part of the project")
            files.update(info.path for info in infos)
        ref, label = _base(project, args.since, args.merge_base, "--since REF")
        changes = changes_from_git(project, ref, False, files)
        changed = sorted(changes.changed.values(), key=lambda s: (s.path, s.lineno))
        removed = sorted(changes.removed.values(), key=lambda s: (s.path, s.lineno))
        added = sorted(changes.added.values(), key=lambda s: (s.path, s.lineno))
        title = f"{title} since {label}"
        if args.function or args.lines:
            warnings.append("--function and --lines are ignored together with --since")
    else:
        changed = select(project, args.targets, args.function, args.lines)
        removed, added = [], []
    report = analyze(project, changed, removed, added, depth, title, IMPACT, warnings)
    return _finish(report, project, args)


def _cmd_diff(args: argparse.Namespace) -> int:
    project, depth = _load(args)
    ref, label = _base(project, args.ref, args.merge_base, "a REF")
    changes = changes_from_git(project, ref, args.staged)
    if args.staged and ref:
        title = f"staged changes vs {label}"
    elif args.staged:
        title = "staged changes"
    elif ref:
        title = f"working tree vs {label}"
    else:
        title = "uncommitted changes"
    report = analyze(
        project,
        sorted(changes.changed.values(), key=lambda s: (s.path, s.lineno)),
        sorted(changes.removed.values(), key=lambda s: (s.path, s.lineno)),
        sorted(changes.added.values(), key=lambda s: (s.path, s.lineno)),
        depth,
        title,
        IMPACT,
    )
    return _finish(report, project, args)


def _cmd_deps(args: argparse.Namespace) -> int:
    project, depth = _load(args)
    targets = select(project, args.targets, args.function)
    report = analyze(project, targets, depth=depth, title=" ".join(args.targets), mode=DEPS)
    return _finish(report, project, args)


def _cmd_symbols(args: argparse.Namespace) -> int:
    project, _ = _load(args)
    rows = []
    for spec in args.targets:
        infos = project.find_modules(spec)
        if not infos:
            raise SelectionError(f"'{spec}' is not part of the project")
        for info in infos:
            for symbol in sorted(info.symbols.values(), key=lambda s: (s.lineno, s.qualname)):
                rows.append(
                    {
                        "symbol": symbol.qualname,
                        "kind": symbol.kind,
                        "path": symbol.path,
                        "lines": [symbol.lineno, symbol.end_lineno],
                        "fan_in": len(project.graph.dependents(symbol.qualname)),
                        "fan_out": len(project.graph.dependencies(symbol.qualname)),
                    }
                )
    if args.format == "json":
        _write(json.dumps(rows, indent=2, ensure_ascii=False) + "\n", args.output)
        return 0
    st = Style(_use_color(args))
    width = max([len(r["symbol"]) for r in rows] + [6])
    lines = [st(f"{'KIND':<10}{'SYMBOL':<{width + 2}}{'LINES':<12}{'IN':>4}{'OUT':>5}", BOLD)]
    for row in rows:
        span = f"{row['lines'][0]}-{row['lines'][1]}"
        lines.append(
            f"{st(row['kind'].ljust(10), DIM)}{row['symbol']:<{width + 2}}{span:<12}{row['fan_in']:>4}{row['fan_out']:>5}"
        )
    _write("\n".join(lines) + "\n", args.output)
    return 0



def main(argv: Optional[Sequence[str]] = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            try:
                reconfigure(errors="replace")
            except (ValueError, OSError):
                pass
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "handler", None):
        parser.print_help()
        return 2
    try:
        return args.handler(args)
    except (SelectionError, GitError, ConfigError) as exc:
        print(f"codeimpact: error: {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        return 130
