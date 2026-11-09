from __future__ import annotations

import json
import shlex
from collections import defaultdict
from typing import Dict, List, Sequence

from .analysis import IMPACT, Hit, Report
from .models import CLASS, LIKELY, MODULE, VARIABLE, ATTRIBUTE, Symbol

FORMATS = ("text", "json", "markdown", "dot", "mermaid")

BOLD = "1"
DIM = "2"
RED = "31"
GREEN = "32"
YELLOW = "33"
BLUE = "34"
MAGENTA = "35"
CYAN = "36"


class Style:
    def __init__(self, enabled: bool) -> None:
        self.enabled = enabled

    def __call__(self, text: str, *codes: str) -> str:
        if not self.enabled or not codes:
            return text
        return f"\033[{';'.join(codes)}m{text}\033[0m"


def render(report: Report, fmt: str = "text", color: bool = False) -> str:
    if fmt == "json":
        return json.dumps(to_dict(report), indent=2, ensure_ascii=False) + "\n"
    if fmt == "markdown":
        return render_markdown(report)
    if fmt == "dot":
        return render_dot(report)
    if fmt == "mermaid":
        return render_mermaid(report)
    return render_text(report, color)


def pytest_command(tests: Sequence[str], limit: int = 12) -> str:
    if not tests:
        return ""
    items = list(tests)
    if len(items) > limit:
        items = sorted({t.split("::", 1)[0] for t in items})
    return "python -m pytest " + " ".join(shlex.quote(t) for t in items)


def _symbol_dict(symbol: Symbol) -> Dict[str, object]:
    return {
        "symbol": symbol.qualname,
        "kind": symbol.kind,
        "module": symbol.module,
        "path": symbol.path,
        "line": symbol.lineno,
        "end_line": symbol.end_lineno,
    }


def to_dict(report: Report) -> Dict[str, object]:
    return {
        "mode": report.mode,
        "title": report.title,
        "root": report.root,
        "summary": {
            "risk": report.risk if report.mode == IMPACT else None,
            "impacted": len(report.impacted),
            "modules": len(report.modules),
            "tests": len(report.tests),
            "max_depth": report.max_depth,
        },
        "changed": [_symbol_dict(s) for s in report.changed],
        "removed": [_symbol_dict(s) for s in report.removed],
        "added": [_symbol_dict(s) for s in report.added],
        "impacted": [
            {
                **_symbol_dict(hit.symbol),
                "depth": hit.depth,
                "via": hit.via,
                "confidence": hit.confidence,
                "reference_line": hit.line,
                "test": hit.nodeid,
                "role": hit.role,
            }
            for hit in report.hits
        ],
        "modules": [{"module": m, "path": p, "count": n} for m, p, n in report.modules],
        "broken_imports": [{"module": m, "path": p} for m, p in report.broken],
        "tests": report.tests,
        "related_tests": report.related_tests,
        "pytest": pytest_command(report.tests),
        "edges": [{"from": a, "to": b, "confidence": c} for a, b, c in report.edges],
        "stats": report.stats,
        "warnings": report.warnings,
    }


def _kind_color(symbol: Symbol) -> str:
    if symbol.kind == CLASS:
        return MAGENTA
    if symbol.kind in (VARIABLE, ATTRIBUTE):
        return BLUE
    if symbol.kind == MODULE:
        return YELLOW
    return CYAN


def _children(report: Report) -> Dict[str, List[Hit]]:
    children: Dict[str, List[Hit]] = defaultdict(list)
    for hit in report.hits:
        children[hit.via].append(hit)
    for items in children.values():
        items.sort(key=lambda h: (h.is_test, h.symbol.qualname))
    return children


def render_text(report: Report, color: bool = False) -> str:
    st = Style(color)
    impact = report.mode == IMPACT
    lines: List[str] = []
    out = lines.append
    out(st("CodeImpact", BOLD, CYAN) + st(" · ", DIM) + st(report.title, BOLD))
    stats = report.stats
    out(st(
        f"{stats['files']} files · {stats['symbols']} symbols · {stats['edges']} edges · {float(stats['elapsed']):.2f}s",
        DIM,
    ))
    for warning in report.warnings:
        out(st(f"! {warning}", YELLOW))
    out("")

    children = _children(report)

    def label(hit: Hit) -> str:
        symbol = hit.symbol
        if hit.is_test:
            name = st(symbol.qualname, GREEN)
            kind = st("test", GREEN, DIM)
        elif hit.role != "code":
            name = st(symbol.qualname, GREEN, DIM)
            kind = st(hit.role, DIM)
        else:
            name = st(symbol.qualname, _kind_color(symbol))
            kind = st(symbol.kind, DIM)
        text = f"{name}  {kind}  {st(hit.location, DIM)}"
        if hit.confidence == LIKELY:
            text += "  " + st("~likely", YELLOW)
        return text

    def branch(qualname: str, indent: str) -> None:
        items = children.get(qualname, [])
        for index, hit in enumerate(items):
            last = index == len(items) - 1
            out(indent + st("└─ " if last else "├─ ", DIM) + label(hit))
            branch(hit.symbol.qualname, indent + ("   " if last else st("│  ", DIM)))

    heading = "Impact" if impact else "Dependencies"
    out(st(heading, BOLD))
    roots = [(s, False) for s in report.changed] + [(s, True) for s in report.removed]
    if not roots:
        out(st("  nothing changed", DIM))
    for symbol, gone in roots:
        marker = st("✕", RED) if gone else st("●", RED if impact else BLUE)
        name = st(symbol.qualname, BOLD)
        tail = st("removed", RED) if gone else st(symbol.kind, DIM)
        suffix = "" if children.get(symbol.qualname) else st("  · no " + ("dependents" if impact else "dependencies"), DIM)
        out(f"  {marker} {name}  {tail}  {st(symbol.location, DIM)}{suffix}")
        branch(symbol.qualname, "    ")
    if report.broken:
        out("")
        out(st(f"Broken imports ({len(report.broken)})", BOLD, RED) + st("  these modules import a removed name and will fail with ImportError", DIM))
        for module, path in report.broken:
            out(f"  {st('✕', RED)} {st(module, RED)}  {st(path, DIM)}")
    if report.added:
        out("")
        out(st(f"Added ({len(report.added)})", BOLD))
        for symbol in report.added:
            out(f"  {st('+', GREEN)} {symbol.qualname}  {st(symbol.kind, DIM)}  {st(symbol.location, DIM)}")

    modules = report.modules
    if modules:
        out("")
        out(st(f"{'Modules affected' if impact else 'Modules used'} ({len(modules)})", BOLD))
        width = max(len(m) for m, _, _ in modules)
        for module, path, count in modules:
            noun = "symbol" if count == 1 else "symbols"
            out(f"  {module.ljust(width)}  {str(count).rjust(3)} {noun}  {st(path, DIM)}")

    if impact:
        out("")
        out(st(f"Tests to run ({len(report.tests)})", BOLD))
        if report.tests:
            for test in report.tests:
                out("  " + st(test, GREEN))
        else:
            out(st("  no tests reference the changed code", DIM))
        if report.related_tests:
            out("")
            out(st(f"Related test files ({len(report.related_tests)})", BOLD))
            for path in report.related_tests:
                out("  " + st(path, DIM))
        out("")
        risk = report.risk
        risk_color = {"none": GREEN, "low": GREEN, "medium": YELLOW, "high": RED}[risk]
        noun = "module" if len(modules) == 1 else "modules"
        summary = (
            f"{len(report.impacted)} symbols · {len(modules)} {noun} · "
            f"{len(report.tests)} tests · depth {report.max_depth}"
        )
        if report.broken:
            summary += f" · {len(report.broken)} broken imports"
        out(st("Risk ", BOLD) + st(risk.upper(), BOLD, risk_color) + st("  " + summary, DIM))
        command = pytest_command(report.tests)
        if command:
            out(st("Run  ", BOLD) + command)
    else:
        out("")
        out(st(f"{len(report.hits)} dependencies · depth {report.max_depth}", DIM))
    return "\n".join(lines) + "\n"


def _md(text: str) -> str:
    return text.replace("|", "\\|")


def render_markdown(report: Report) -> str:
    impact = report.mode == IMPACT
    lines: List[str] = []
    out = lines.append
    out(f"## CodeImpact: `{report.title}`")
    out("")
    if impact:
        out(
            f"**Risk:** {report.risk} · **{len(report.impacted)}** impacted symbols in "
            f"**{len(report.modules)}** modules · **{len(report.tests)}** tests · max depth {report.max_depth}"
        )
    else:
        out(f"**{len(report.hits)}** dependencies · max depth {report.max_depth}")
    out("")
    for warning in report.warnings:
        out(f"> ⚠️ {warning}")
        out("")
    if report.changed or report.removed:
        out("### Changed" if impact else "### Targets")
        out("")
        out("| Symbol | Kind | Location |")
        out("| --- | --- | --- |")
        for symbol in report.changed:
            out(f"| `{_md(symbol.qualname)}` | {symbol.kind} | `{symbol.location}` |")
        for symbol in report.removed:
            out(f"| `{_md(symbol.qualname)}` | removed {symbol.kind} | `{symbol.location}` |")
        out("")
    if report.broken:
        out("### ⛔ Broken imports")
        out("")
        out("These modules import a removed name and will fail with `ImportError`:")
        out("")
        for module, path in report.broken:
            out(f"- `{module}` (`{path}`)")
        out("")
    if report.added:
        out("<details><summary>Added symbols</summary>")
        out("")
        for symbol in report.added:
            out(f"- `{symbol.qualname}` ({symbol.kind}, `{symbol.location}`)")
        out("")
        out("</details>")
        out("")
    if report.hits:
        out("### Impacted" if impact else "### Dependencies")
        out("")
        out("| Depth | Symbol | Kind | Location | Via | Confidence |")
        out("| ---: | --- | --- | --- | --- | --- |")
        for hit in report.hits:
            kind = hit.role if hit.role != "code" else hit.symbol.kind
            out(
                f"| {hit.depth} | `{_md(hit.symbol.qualname)}` | {kind} | `{hit.location}` "
                f"| `{_md(hit.via)}` | {hit.confidence} |"
            )
        out("")
    if impact and report.modules:
        out("### Modules affected")
        out("")
        for module, path, count in report.modules:
            out(f"- `{module}` — {count} symbol{'s' if count != 1 else ''} (`{path}`)")
        out("")
    if impact:
        out("### Tests to run")
        out("")
        if report.tests:
            out("```bash")
            out(pytest_command(report.tests, limit=40))
            out("```")
        else:
            out("_No tests reference the changed code._")
        if report.related_tests:
            out("")
            out("Related test files: " + ", ".join(f"`{p}`" for p in report.related_tests))
        out("")
    return "\n".join(lines)


def _graph_nodes(report: Report) -> List[Symbol]:
    seen: Dict[str, Symbol] = {}
    for symbol in report.changed + report.removed:
        seen[symbol.qualname] = symbol
    for hit in report.hits:
        seen.setdefault(hit.symbol.qualname, hit.symbol)
    return list(seen.values())


def render_dot(report: Report) -> str:
    changed = {s.qualname for s in report.changed}
    removed = {s.qualname for s in report.removed}
    tests = {h.symbol.qualname for h in report.hits if h.is_test}
    lines = [
        "digraph codeimpact {",
        "  rankdir=LR;",
        '  graph [fontname="Helvetica", fontsize=11];',
        '  node [shape=box, style="rounded,filled", fontname="Helvetica", fontsize=10, fillcolor="#eef2ff", color="#6366f1"];',
        '  edge [color="#94a3b8", arrowsize=0.7];',
    ]
    for symbol in _graph_nodes(report):
        attrs = [f'label="{symbol.qualname}\\n{symbol.kind} · {symbol.location}"']
        if symbol.qualname in removed:
            attrs.append('fillcolor="#fecaca", color="#dc2626", style="rounded,filled,dashed"')
        elif symbol.qualname in changed:
            attrs.append('fillcolor="#fee2e2", color="#dc2626", penwidth=2')
        elif symbol.qualname in tests:
            attrs.append('fillcolor="#dcfce7", color="#16a34a"')
        lines.append(f'  "{symbol.qualname}" [{", ".join(attrs)}];')
    for dependent, dependency, confidence in report.edges:
        style = ' [style=dashed, color="#f59e0b"]' if confidence == LIKELY else ""
        lines.append(f'  "{dependency}" -> "{dependent}"{style};')
    lines.append("}")
    return "\n".join(lines) + "\n"


def render_mermaid(report: Report) -> str:
    changed = {s.qualname for s in report.changed}
    removed = {s.qualname for s in report.removed}
    tests = {h.symbol.qualname for h in report.hits if h.is_test}
    ids: Dict[str, str] = {}
    lines = ["flowchart LR"]
    for index, symbol in enumerate(_graph_nodes(report)):
        node = f"n{index}"
        ids[symbol.qualname] = node
        text = symbol.qualname.replace('"', "'")
        lines.append(f'  {node}["{text}"]')
        if symbol.qualname in removed:
            lines.append(f"  class {node} removed")
        elif symbol.qualname in changed:
            lines.append(f"  class {node} changed")
        elif symbol.qualname in tests:
            lines.append(f"  class {node} test")
    for dependent, dependency, confidence in report.edges:
        if dependent in ids and dependency in ids:
            arrow = "-.->" if confidence == LIKELY else "-->"
            lines.append(f"  {ids[dependency]} {arrow} {ids[dependent]}")
    lines.append("  classDef changed fill:#fee2e2,stroke:#dc2626,stroke-width:2px")
    lines.append("  classDef removed fill:#fecaca,stroke:#dc2626,stroke-dasharray:4")
    lines.append("  classDef test fill:#dcfce7,stroke:#16a34a")
    return "\n".join(lines) + "\n"
