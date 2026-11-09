# CodeImpact

**Изменил одну функцию — увидь всё, что от неё зависит.**

Статический анализ последствий изменений для Python. CodeImpact строит граф вызовов и ссылок по AST, идёт по нему в обратную сторону от изменённого кода и показывает, какие функции, классы и модули от него зависят — и какие именно pytest-тесты нужно запустить.

[English version](README.md)

```bash
codeimpact analyze src/auth.py
```

## Возможности

- **Настоящее разрешение имён, а не grep.** Импорты с алиасами, относительные импорты, реэкспорт через `__init__.py`, `from x import *`, вложенные классы, константы модулей.
- **Понимает классы.** `self.method()`, `cls.method()`, `super()`, наследование (MRO), конструкторы (`User(...)` зависит от `User.__init__`), атрибуты классов и поля dataclass.
- **Лёгкий вывод типов.** Аннотации параметров (`Optional[Repo]`, `Repo | None`, `"Repo"`), `x = Repo()`, `with Lock() as lock`, `self.repo = Repo()` — поэтому `self.repo.save()` разрешается в `Repo.save`.
- **Выбор тестов с учётом pytest.** Тестовые функции, классы `Test*`, наследники `unittest.TestCase` с любым именем, фикстуры из `conftest.py` вверх по дереву каталогов, `@pytest.fixture(name=...)`, `autouse=True` фикстуры (с учётом переопределений), `@pytest.mark.usefixtures(...)`, цели `mock.patch("pkg.mod.func")`. На выходе — готовые node id для pytest.
- **Интеграция с git.** `codeimpact diff` сопоставляет дифф с конкретными функциями. Правки только комментариев и пробелов игнорируются. **Удалённые** функции прослеживаются до мест, которые всё ещё их вызывают, а модули, которые упадут с `ImportError` (и всё, что их импортирует), показываются как *broken imports*.
- **Честная уверенность.** Вызовы на объектах без известного типа (`obj.deliver_mail()`) связываются только если имя метода уникально в проекте, и помечаются `~likely`. `--strict` это отключает.
- **Форматы вывода:** цветное дерево, JSON, Markdown (для PR и `$GITHUB_STEP_SUMMARY`), Graphviz DOT, Mermaid.
- **Быстро.** Инкрементальный кэш разбора: ~400 файлов / 8.6 тыс. символов — меньше 2 с с нуля и ~0.3 с с кэшем.
- **Без зависимостей** (только `tomli` на Python < 3.11 для чтения `pyproject.toml`).

## Установка

```bash
git clone https://github.com/Sneemok/CodeImpact.git
cd CodeImpact
pip install -e ".[dev]"
```

## Использование

```bash
codeimpact analyze src/auth.py                      # все символы файла
codeimpact analyze src/auth.py -f login             # одна функция
codeimpact analyze src/auth.py::Session.refresh     # цель в стиле pytest
codeimpact analyze src/auth.py --lines 40-58        # символы на этих строках
codeimpact analyze src/auth.py --since main         # только изменённое с ветки main

codeimpact diff                                     # незакоммиченные изменения
codeimpact diff --staged --run                      # и сразу запустить нужные тесты
codeimpact diff origin/main --merge-base            # только то, что изменила ваша ветка
codeimpact diff origin/main --format markdown -o impact.md

pytest $(codeimpact analyze src/auth.py --tests-only)

codeimpact deps src/api.py -f handle_login          # обратное направление: от чего зависит
codeimpact symbols src/auth.py                      # таблица fan-in / fan-out
```

Основные опции: `--depth N`, `--strict`, `--fuzzy N`, `--format text|json|markdown|dot|mermaid`, `-o FILE`, `--tests-only`, `--run`, `--fail-over N`, `--merge-base`, `--root DIR`, `--exclude GLOB`, `--no-cache`.

Коды выхода: `0` — успех, `1` — превышен `--fail-over`, `2` — ошибка аргументов; с `--run` возвращается код pytest.

## Настройка

```toml
[tool.codeimpact]
exclude = ["migrations", "scripts/*"]
test-patterns = ["test_*.py", "*_test.py"]
depth = 0
fuzzy = 1
```

## Как это работает

1. Находит все `.py` файлы и вычисляет их имя модуля так же, как Python (`src/auth.py` → `auth`, пакеты — по `__init__.py`).
2. Разбирает каждый файл через `ast`: символы с точными диапазонами строк, все ссылки `a.b.c` внутри каждого символа, таблицу импортов, локальные имена и выведенные типы.
3. Разрешает каждую ссылку в символ: локальная область → определения модуля → импорты → star-импорты, затем реэкспорт, члены классов, MRO, типы атрибутов.
4. Строит граф `зависимый → зависимость` плюс неявные рёбра: класс → конструктор, класс → атрибуты, тест → фикстура.
5. Обходит граф в ширину назад от изменённых символов; для каждого узла запоминается ребро, по которому он найден, — отсюда дерево «почему».

## Ограничения

`getattr`, `importlib`, DI-контейнеры, monkey-patching и диспетчеризация по строкам статически не видны. Вызовы на объектах без выводимого типа либо помечаются `~likely`, либо пропускаются.

## Демо

```bash
cd examples/demo
codeimpact analyze src/auth.py
pytest
```

## Лицензия

[MIT](LICENSE)
