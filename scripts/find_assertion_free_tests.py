"""
Detect test functions that contain no real verification.

A test function is flagged as "assertion-free" when it contains NONE of:
- ``assert`` statement
- ``pytest.raises(...)`` / ``with pytest.raises(...):``
- ``self.assertX(...)`` (unittest style) or ``self.fail(...)``
- ``mock.assert_called*`` / ``Mock.assert_called*`` calls
- ``mock_X.method.assert_*`` patterns
- ``pytest.fail(...)``
- A bare ``raise`` (negative test that re-raises) — kept conservative

Such functions either rely on "did not crash" as the only signal or are
pure smoke tests. Both are weak verification and worth auditing.

Output is grouped by directory to make triage easier.

Usage:
    python scripts/find_assertion_free_tests.py [tests/unit] [--json] [--count]
"""

from __future__ import annotations

import argparse
import ast
import json
import sys
from collections import defaultdict
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent

# Names that count as a verification call when seen as Call.func.attr
_ASSERT_METHOD_PREFIXES = (
    "assert",  # unittest.TestCase.assertX, mock.assert_called*
    "fail",  # self.fail, pytest.fail
)

# Top-level callable names that count as verification
_VERIFICATION_CALLS = {
    "raises",  # pytest.raises (used as context manager or call)
    "warns",  # pytest.warns
    "deprecated_call",
    "fail",  # pytest.fail / self.fail
}


class _AssertionFinder(ast.NodeVisitor):
    """Detect any verification primitive inside a function body."""

    def __init__(self):
        self.found = False

    # `assert expr` statement
    def visit_Assert(self, node):  # noqa: N802 — ast API
        self.found = True

    # `with pytest.raises(...)` and `pytest.raises(...).__enter__`
    def visit_With(self, node):  # noqa: N802
        for item in node.items:
            if self._is_verification_call(item.context_expr):
                self.found = True
                break
        self.generic_visit(node)

    def visit_AsyncWith(self, node):  # noqa: N802
        self.visit_With(node)

    def visit_Call(self, node):  # noqa: N802
        if self._is_verification_call(node):
            self.found = True
        self.generic_visit(node)

    def _is_verification_call(self, node) -> bool:
        if not isinstance(node, ast.Call):
            return False
        func = node.func

        # foo.assertX(...) / mock.assert_called* / pytest.raises(...) / pytest.fail(...)
        if isinstance(func, ast.Attribute):
            name = func.attr
            if name.startswith(_ASSERT_METHOD_PREFIXES):
                return True
            if name in _VERIFICATION_CALLS:
                return True
            return False

        # raises(...), fail(...) (rare — usually accessed via attribute)
        if isinstance(func, ast.Name) and func.id in _VERIFICATION_CALLS:
            return True

        return False


def is_test_function(node: ast.AST) -> bool:
    return isinstance(
        node, (ast.FunctionDef, ast.AsyncFunctionDef)
    ) and node.name.startswith("test_")


def has_assertion(func_node: ast.AST) -> bool:
    finder = _AssertionFinder()
    for child in func_node.body:
        finder.visit(child)
        if finder.found:
            return True
    return False


def scan_file(path: Path) -> list[tuple[int, str]]:
    """Return [(lineno, function_name), ...] for assertion-free tests."""
    try:
        source = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return []
    try:
        tree = ast.parse(source, filename=str(path))
    except SyntaxError:
        return []

    flagged: list[tuple[int, str]] = []

    def _walk(scope, prefix=""):
        for node in scope.body:
            if is_test_function(node):
                if not has_assertion(node):
                    flagged.append((node.lineno, f"{prefix}{node.name}"))
            elif isinstance(node, ast.ClassDef):
                _walk(node, prefix=f"{node.name}.")

    _walk(tree)
    return flagged


def main() -> int:
    parser = argparse.ArgumentParser(description="Find assertion-free test functions")
    parser.add_argument(
        "root",
        nargs="?",
        default="tests/unit",
        help="Test root directory (default: tests/unit)",
    )
    parser.add_argument("--json", action="store_true", help="JSON output")
    parser.add_argument(
        "--count", action="store_true", help="Print only summary counts"
    )
    args = parser.parse_args()

    root = (PROJECT_ROOT / args.root).resolve()
    if not root.exists():
        print(f"ERROR: {root} does not exist", file=sys.stderr)
        return 2

    by_file: dict[str, list[tuple[int, str]]] = defaultdict(list)
    total = 0
    files_scanned = 0

    for py in sorted(root.rglob("test_*.py")):
        files_scanned += 1
        flagged = scan_file(py)
        if flagged:
            rel = str(py.relative_to(PROJECT_ROOT))
            by_file[rel].extend(flagged)
            total += len(flagged)

    if args.json:
        out = {
            "files_scanned": files_scanned,
            "files_flagged": len(by_file),
            "total_flagged_tests": total,
            "results": {f: items for f, items in sorted(by_file.items())},
        }
        print(json.dumps(out, indent=2, ensure_ascii=False))
        return 1 if total else 0

    if args.count:
        print(
            f"Scanned {files_scanned} files, flagged {total} tests in {len(by_file)} files"
        )
        return 1 if total else 0

    if not by_file:
        print(f"No assertion-free tests found across {files_scanned} files.")
        return 0

    print(f"=== Assertion-free tests ({total} in {len(by_file)} files) ===\n")
    for fpath, items in sorted(by_file.items()):
        print(f"{fpath}")
        for lineno, name in items:
            print(f"  L{lineno}: {name}")
        print()

    return 1


if __name__ == "__main__":
    sys.exit(main())
