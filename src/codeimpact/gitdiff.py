from __future__ import annotations

import io
import re
import subprocess
import tokenize
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional, Tuple, Union

HUNK = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")


class GitError(RuntimeError):
    pass


@dataclass
class Hunk:
    old_start: int
    old_count: int
    new_start: int
    new_count: int
    lines: List[str] = field(default_factory=list)

    def _side(self, sign: str, start: int) -> List[Tuple[int, str]]:
        body = [line[1:] for line in self.lines if line.startswith(sign)]
        return [(start + index, normalize(text)) for index, text in enumerate(body)]

    def removed(self) -> List[Tuple[int, str]]:
        return self._side("-", self.old_start)

    def inserted(self) -> List[Tuple[int, str]]:
        return self._side("+", self.new_start)

    @property
    def significant(self) -> bool:
        before = Counter(text for _, text in self.removed() if text)
        after = Counter(text for _, text in self.inserted() if text)
        return before != after

    def old_lines(self) -> List[int]:
        return [number for number, text in self.removed() if text]

    def new_lines(self) -> List[int]:
        return [number for number, text in self.inserted() if text]


_SKIP_TOKENS = {tokenize.COMMENT, tokenize.NL, tokenize.NEWLINE, tokenize.INDENT, tokenize.DEDENT, tokenize.ENDMARKER}


def normalize(line: str) -> str:
    stripped = line.strip()
    if not stripped or stripped.startswith("#"):
        return ""
    try:
        tokens = [
            token.string
            for token in tokenize.generate_tokens(io.StringIO(stripped + "\n").readline)
            if token.type not in _SKIP_TOKENS
        ]
    except (tokenize.TokenError, IndentationError, SyntaxError):
        return " ".join(stripped.split())
    return " ".join(tokens)


@dataclass
class FileDiff:
    old_path: Optional[str] = None
    new_path: Optional[str] = None
    hunks: List[Hunk] = field(default_factory=list)


def git(cwd: Path, *args: str, binary: bool = False) -> Union[str, bytes]:
    try:
        process = subprocess.run(
            ["git", "-c", "core.quotepath=off", *args],
            cwd=str(cwd),
            capture_output=True,
            check=False,
        )
    except FileNotFoundError as exc:
        raise GitError("git executable not found") from exc
    if process.returncode != 0:
        message = process.stderr.decode("utf-8", errors="replace").strip()
        raise GitError(message or f"git {args[0]} failed with exit code {process.returncode}")
    if binary:
        return process.stdout
    return process.stdout.decode("utf-8", errors="replace")


def toplevel(cwd: Path) -> Path:
    return Path(str(git(cwd, "rev-parse", "--show-toplevel")).strip()).resolve()


def diff(cwd: Path, ref: Optional[str] = None, staged: bool = False) -> List[FileDiff]:
    args = ["diff", "--no-color", "--no-ext-diff", "--no-renames", "-U0"]
    if staged:
        args.append("--cached")
    if ref:
        args.append(ref)
    args.extend(["--", "*.py"])
    return parse(str(git(cwd, *args)))


def untracked(cwd: Path) -> List[str]:
    output = str(git(cwd, "ls-files", "--others", "--exclude-standard", "--full-name", "--", "*.py"))
    return [line for line in output.splitlines() if line.strip()]


def show(cwd: Path, spec: str) -> Optional[bytes]:
    try:
        data = git(cwd, "show", spec, binary=True)
    except GitError:
        return None
    return data if isinstance(data, bytes) else data.encode("utf-8")


def parse(text: str) -> List[FileDiff]:
    files: List[FileDiff] = []
    current: Optional[FileDiff] = None
    hunk: Optional[Hunk] = None
    for line in text.splitlines():
        if line.startswith("diff --git "):
            current = FileDiff()
            files.append(current)
            hunk = None
            continue
        if current is None:
            continue
        if hunk is None and line.startswith("--- "):
            current.old_path = _path(line[4:], "a/")
            continue
        if hunk is None and line.startswith("+++ "):
            current.new_path = _path(line[4:], "b/")
            continue
        match = HUNK.match(line)
        if match:
            hunk = Hunk(
                int(match.group(1)),
                int(match.group(2)) if match.group(2) is not None else 1,
                int(match.group(3)),
                int(match.group(4)) if match.group(4) is not None else 1,
            )
            current.hunks.append(hunk)
            continue
        if hunk is not None and line and line[0] in "+-":
            hunk.lines.append(line)
    return [f for f in files if f.old_path or f.new_path]


def _path(raw: str, prefix: str) -> Optional[str]:
    value = raw.rstrip("\t").strip()
    if value == "/dev/null":
        return None
    if len(value) >= 2 and value[0] == '"' and value[-1] == '"':
        value = _unquote(value[1:-1])
    if value.startswith(prefix):
        value = value[len(prefix):]
    return value


def _unquote(value: str) -> str:
    try:
        return value.encode("latin-1", errors="backslashreplace").decode("unicode_escape").encode("latin-1").decode("utf-8")
    except (UnicodeDecodeError, UnicodeEncodeError):
        return value
