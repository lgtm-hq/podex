"""Evaluate GitHub Actions expressions for the workflow contract tests.

Implements the subset of the expression language that workflow concurrency
blocks use, with GitHub's semantics: literals (strings, numbers, true, false,
null), context property access, ``!``, ``==``, ``!=``, ``&&``, ``||``,
parentheses and the functions ``format``, ``startsWith``, ``endsWith`` and
``contains``. ``&&`` and ``||`` return an operand, not a boolean, string
comparison ignores case, operands of different types compare as numbers, and
a missing property is null, which renders as an empty string. Anything outside
the subset is an error, so an unsupported construct fails the test instead of
being guessed at.

Usage:
    github_expression.py render TEMPLATE CONTEXT_JSON
    github_expression.py truthy TEMPLATE CONTEXT_JSON

``render`` prints TEMPLATE with each ``${{ ... }}`` replaced by its value.
``truthy`` prints ``true`` or ``false`` for a value that GitHub reads as a
boolean, such as ``cancel-in-progress``: a single expression, or the literal
``true`` or ``false``. CONTEXT_JSON maps context names (``github``,
``inputs``, ...) to their values.
"""

from __future__ import annotations

import json
import math
import re
import sys
from collections.abc import Callable

Value = str | float | bool | None | dict[str, object] | list[object]

_TOKEN_RE = re.compile(
    r"""
    \s*(?:
      (?P<string>'(?:[^']|'')*')
    | (?P<number>\d+(?:\.\d+)?)
    | (?P<name>[A-Za-z_][A-Za-z0-9_-]*)
    | (?P<op>==|!=|&&|\|\||[!().,])
    )
    """,
    re.VERBOSE,
)
_FORMAT_RE = re.compile(r"\{\{|\}\}|\{(\d+)\}")
_KEYWORDS: dict[str, Value] = {"true": True, "false": False, "null": None}


class ExpressionError(ValueError):
    """Raised for input outside the supported expression subset."""


def _tokenize(text: str) -> list[tuple[str, str]]:
    tokens: list[tuple[str, str]] = []
    position = 0
    text = text.rstrip()
    while position < len(text):
        match = _TOKEN_RE.match(text, position)
        if match is None or match.lastgroup is None:
            raise ExpressionError(f"unsupported syntax at: {text[position:]!r}")
        tokens.append((match.lastgroup, match.group(match.lastgroup)))
        position = match.end()
    return tokens


def _to_number(value: Value) -> float:
    if value is None or isinstance(value, bool):
        return float(bool(value))
    if isinstance(value, float):
        return value
    if not isinstance(value, str):
        return math.nan
    try:
        return float(value.strip() or 0)
    except ValueError:
        return math.nan


def _to_string(value: Value) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, float):
        return str(int(value)) if value.is_integer() else str(value)
    if isinstance(value, str):
        return value
    return json.dumps(value)


def _truthy(value: Value) -> bool:
    if value is None:
        return False
    if isinstance(value, bool):
        return value
    if isinstance(value, float):
        return value != 0 and not math.isnan(value)
    if isinstance(value, str):
        return value != ""
    return True


def _equal(left: Value, right: Value) -> bool:
    if isinstance(left, str) and isinstance(right, str):
        return left.casefold() == right.casefold()
    if left is None and right is None:
        return True
    if isinstance(left, bool) and isinstance(right, bool):
        return left == right
    if isinstance(left, dict | list) or isinstance(right, dict | list):
        return left is right
    return _to_number(left) == _to_number(right)


def _format(template: Value, *args: Value) -> Value:
    def replace(match: re.Match[str]) -> str:
        if match.group(0) == "{{":
            return "{"
        if match.group(0) == "}}":
            return "}"
        index = int(match.group(1))
        if index >= len(args):
            raise ExpressionError(f"format index {index} has no argument")
        return _to_string(args[index])

    return _FORMAT_RE.sub(replace, _to_string(template))


def _string_test(test: Callable[[str, str], bool]) -> Callable[..., Value]:
    def apply(*args: Value) -> Value:
        if len(args) != 2:
            raise ExpressionError("function takes two arguments")
        return test(_to_string(args[0]).casefold(), _to_string(args[1]).casefold())

    return apply


_FUNCTIONS: dict[str, Callable[..., Value]] = {
    "format": _format,
    "startswith": _string_test(str.startswith),
    "endswith": _string_test(str.endswith),
    "contains": _string_test(lambda haystack, needle: needle in haystack),
}


class _Parser:
    """Recursive-descent evaluator over one tokenized expression."""

    def __init__(self, tokens: list[tuple[str, str]], context: dict[str, Value]):
        self._tokens = tokens
        self._index = 0
        self._context = context

    def evaluate(self) -> Value:
        """Evaluate the whole token list; trailing tokens are an error."""
        value = self._or()
        if self._index != len(self._tokens):
            raise ExpressionError(f"unexpected token: {self.peek()[1]!r}")
        return value

    def peek(self) -> tuple[str, str]:
        """Return the next token without consuming it."""
        if self._index < len(self._tokens):
            return self._tokens[self._index]
        return ("end", "")

    def accept(self, operator: str) -> bool:
        """Consume the next token if it is OPERATOR."""
        if self.peek() == ("op", operator):
            self._index += 1
            return True
        return False

    def expect(self, operator: str) -> None:
        """Consume OPERATOR or raise."""
        if not self.accept(operator):
            raise ExpressionError(f"expected {operator!r}, got {self.peek()[1]!r}")

    def _or(self) -> Value:
        value = self._and()
        while self.accept("||"):
            right = self._and()
            value = value if _truthy(value) else right
        return value

    def _and(self) -> Value:
        value = self._equality()
        while self.accept("&&"):
            right = self._equality()
            value = right if _truthy(value) else value
        return value

    def _equality(self) -> Value:
        value = self._unary()
        while self.peek() in (("op", "=="), ("op", "!=")):
            operator = self.peek()[1]
            self._index += 1
            right = self._unary()
            value = _equal(value, right) == (operator == "==")
        return value

    def _unary(self) -> Value:
        if self.accept("!"):
            return not _truthy(self._unary())
        return self._primary()

    def _primary(self) -> Value:
        kind, text = self.peek()
        self._index += 1
        if kind == "string":
            return text[1:-1].replace("''", "'")
        if kind == "number":
            return float(text)
        if kind == "op" and text == "(":
            value = self._or()
            self.expect(")")
            return value
        if kind == "name":
            if text in _KEYWORDS:
                return _KEYWORDS[text]
            if self.accept("("):
                return self._call(text)
            return self._property(text)
        raise ExpressionError(f"unexpected token: {text!r}")

    def _call(self, name: str) -> Value:
        function = _FUNCTIONS.get(name.lower())
        if function is None:
            raise ExpressionError(f"unsupported function: {name}")
        args: list[Value] = []
        if not self.accept(")"):
            args.append(self._or())
            while self.accept(","):
                args.append(self._or())
            self.expect(")")
        return function(*args)

    def _property(self, name: str) -> Value:
        if name not in self._context:
            raise ExpressionError(f"unknown context: {name}")
        value: object = self._context[name]
        while self.accept("."):
            kind, key = self.peek()
            if kind != "name":
                raise ExpressionError(f"expected a property name after {name!r}")
            self._index += 1
            value = value.get(key) if isinstance(value, dict) else None
        if isinstance(value, int) and not isinstance(value, bool):
            return float(value)
        if value is None or isinstance(value, str | float | bool | dict | list):
            return value
        raise ExpressionError(f"unsupported context value for {name}")


def evaluate(expression: str, context: dict[str, Value]) -> Value:
    """Evaluate one expression (the text between ``${{`` and ``}}``)."""
    tokens = _tokenize(expression)
    if not tokens:
        raise ExpressionError("empty expression")
    return _Parser(tokens, context).evaluate()


def _split_template(template: str) -> list[tuple[bool, str]]:
    """Split TEMPLATE into (is_expression, text) parts.

    The closing ``}}`` of an expression is searched outside string literals,
    so ``format('{{0}}', ...)`` stays one expression.
    """
    parts: list[tuple[bool, str]] = []
    position = 0
    while (start := template.find("${{", position)) != -1:
        if start > position:
            parts.append((False, template[position:start]))
        cursor = start + 3
        in_string = False
        while cursor < len(template):
            if template[cursor] == "'":
                in_string = not in_string
            elif not in_string and template.startswith("}}", cursor):
                break
            cursor += 1
        else:
            raise ExpressionError(f"unterminated expression: {template[start:]!r}")
        parts.append((True, template[start + 3 : cursor]))
        position = cursor + 2
    if position < len(template):
        parts.append((False, template[position:]))
    return parts


def render(template: str, context: dict[str, Value]) -> str:
    """Return TEMPLATE with every ``${{ ... }}`` replaced by its value."""
    return "".join(
        _to_string(evaluate(text, context)) if is_expression else text
        for is_expression, text in _split_template(template)
    )


def truthy(template: str, context: dict[str, Value]) -> bool:
    """Return the boolean GitHub reads from a single-expression value."""
    stripped = template.strip()
    if stripped in ("true", "false"):
        return stripped == "true"
    parts = _split_template(stripped)
    if len(parts) != 1 or not parts[0][0]:
        raise ExpressionError(f"not a single expression or boolean: {template!r}")
    return _truthy(evaluate(parts[0][1], context))


def main(argv: list[str]) -> int:
    """Run the command line interface described in the module docstring."""
    if len(argv) != 4 or argv[1] not in ("render", "truthy"):
        print(__doc__, file=sys.stderr)
        return 2
    context = json.loads(argv[3])
    if not isinstance(context, dict):
        print("CONTEXT_JSON must be an object", file=sys.stderr)
        return 2
    try:
        if argv[1] == "render":
            print(render(argv[2], context))
        else:
            print("true" if truthy(argv[2], context) else "false")
    except ExpressionError as error:
        print(f"expression error: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
