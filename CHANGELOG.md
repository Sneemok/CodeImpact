# Changelog

## 0.1.0 — 2026-11-09

First release.

- `analyze`, `diff`, `deps` and `symbols` commands.
- AST-based symbol extraction: modules, classes, functions, methods, module variables, class attributes.
- Name resolution through aliases, relative imports, `__init__.py` re-exports, star imports, MRO and `super()`.
- Type inference from annotations, constructor assignments, `with ... as`, and `self.x = ...` attributes.
- Pytest test selection with fixtures, `conftest.py` chains, `usefixtures` and `mock.patch` targets.
- Git diff mapping that ignores comment-only edits and traces callers of deleted symbols.
- Broken-import detection: modules importing a removed name, and their importers, are reported and force HIGH risk.
- Text, JSON, Markdown, DOT and Mermaid output; `--tests-only`, `--run`, `--fail-over`.
- Incremental parse cache in `.codeimpact_cache/`.
