"""
Which uploaded .py files pytest should be asked to collect.

pytest's directory discovery only collects files named ``test_*.py`` or ``*_test.py``.
Uploaded Selenium IDE exports are frequently named otherwise (``tes.py``, ``tes(1).py``
after a duplicate download, ``login.py``), and for those pytest runs nothing and exits
with code 5 ("no tests ran") before a single line of the script is imported.

This module decides, by static analysis only (the source is parsed, never executed and
never modified), which files define tests, so they can be handed to pytest explicitly.
Helper modules (page objects, data files) that define no tests are left alone, exactly
as before: they are still imported by the tests that use them.
"""
import ast
import fnmatch
from pathlib import Path

# pytest's defaults for the python_files option.
_DEFAULT_PATTERNS = ("test_*.py", "*_test.py")
_CONFTEST = "conftest.py"


def matches_default_pattern(name: str) -> bool:
    return any(fnmatch.fnmatch(name, pattern) for pattern in _DEFAULT_PATTERNS)


def _defines_tests(tree: ast.AST) -> bool:
    """True when pytest would collect something from this module: a top-level test*
    function, a Test* class with test* methods, or a unittest.TestCase subclass."""
    for node in getattr(tree, "body", []):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith("test"):
            return True
        if isinstance(node, ast.ClassDef):
            has_test_method = any(
                isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)) and child.name.startswith("test")
                for child in node.body
            )
            is_testcase = any(
                (isinstance(base, ast.Name) and base.id == "TestCase")
                or (isinstance(base, ast.Attribute) and base.attr == "TestCase")
                for base in node.bases
            )
            if is_testcase or (node.name.startswith("Test") and has_test_method):
                return True
    return False


def file_defines_tests(path: Path) -> bool:
    try:
        tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
    except (SyntaxError, ValueError, OSError, RecursionError):
        return False  # unparsable: not guessed at (a test named test_*.py still reports its error)
    return _defines_tests(tree)


def select_test_files(directory: Path) -> tuple[list[Path], list[Path]]:
    """(files to collect, the subset of them that pytest would NOT have found by name).

    The second list is empty for ordinary uploads, in which case the caller keeps the
    plain directory run."""
    selected: list[Path] = []
    by_content: list[Path] = []
    for path in sorted(directory.glob("*.py")):
        if path.name == _CONFTEST:
            continue
        if matches_default_pattern(path.name):
            selected.append(path)
        elif file_defines_tests(path):
            selected.append(path)
            by_content.append(path)
    return selected, by_content
