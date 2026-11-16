"""Evaluate GitHub Actions expressions for the workflow contract tests.

Implements the subset of the expression language that workflow concurrency
blocks use, with GitHub's semantics: literals (strings, numbers, true, false,
null), context property access, ``!``, ``==``, ``!=``, ``&&``, ``||``,
parentheses and the functions ``format``, ``startsWith``, ``endsWith`` and
``contains``. An expression is parsed in full before it is evaluated: syntax,
unknown functions, wrong argument counts and unknown contexts are errors even
in an operand that is never evaluated. ``&&`` and ``||`` short-circuit and
return an operand, not a boolean: ``a && b`` is ``a`` when ``a`` is falsy and
otherwise ``b``; ``a || b`` is ``a`` when ``a`` is truthy and otherwise ``b``.
The falsy values are ``false``, ``0``, ``NaN``, ``''`` and ``null``. String
comparison ignores case, and operands of different types compare as numbers:
null is 0, booleans are 1 and 0, a string is its JSON number (after trimming
whitespace; empty is 0) or NaN, and an object or array is NaN; NaN equals
nothing. A missing property is null, which renders as an empty string.
Anything outside the subset is an error, so an unsupported construct fails the
test instead of being guessed at. That includes strings the runner reads as
numbers beyond the documented JSON format (``'+1'``, ``'.5'``, ``'007'``,
``'0x10'``, ``'0o7'``, ``'Infinity'``) when they are compared with a non-string.

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
Thunk = Callable[[], Value]

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
_JSON_NUMBER_RE = re.compile(r"-?(?:0|[1-9]\d*)(?:\.\d+)?(?:[eE][+-]?\d+)?")
# Read as numbers by the runner but not by the documented JSON number format.
_UNDOCUMENTED_NUMBER_RE = re.compile(
    r"[+-]?(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?|0x[0-9a-fA-F]+|0o[0-7]+"
    r"|[+-]?(?i:infinity)",
)
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
    text = value.strip()
    if not text:
        return 0.0
    if _JSON_NUMBER_RE.fullmatch(text):
        return float(text)
    if _UNDOCUMENTED_NUMBER_RE.fullmatch(text):
        raise ExpressionError(f"undocumented string-to-number coercion: {value!r}")
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
    def apply(left: Value, right: Value) -> Value:
        return test(_to_string(left).casefold(), _to_string(right).casefold())

    return apply


# Name (lower case) -> (implementation, minimum and maximum argument count).
_FUNCTIONS: dict[str, tuple[Callable[..., Value], int, float]] = {
    "format": (_format, 1, math.inf),
    "startswith": (_string_test(str.startswith), 2, 2),
    "endswith": (_string_test(str.endswith), 2, 2),
    "contains": (_string_test(lambda haystack, needle: needle in haystack), 2, 2),
}


def _constant(value: Value) -> Thunk:
    return lambda: value


def _either(left: Thunk, right: Thunk) -> Thunk:
    """``left || right``: RIGHT is evaluated only when LEFT is falsy."""

    def run() -> Value:
        value = left()
        return value if _truthy(value) else right()

    return run


def _both(left: Thunk, right: Thunk) -> Thunk:
    """``left && right``: RIGHT is evaluated only when LEFT is truthy."""

    def run() -> Value:
        value = left()
        return right() if _truthy(value) else value

    return run


def _compare(left: Thunk, right: Thunk, *, negate: bool) -> Thunk:
    return lambda: _equal(left(), right()) != negate


def _negation(operand: Thunk) -> Thunk:
    return lambda: not _truthy(operand())


def _call(function: Callable[..., Value], args: list[Thunk]) -> Thunk:
    return lambda: function(*(arg() for arg in args))


def _lookup(root: object, keys: list[str], name: str) -> Thunk:
    def run() -> Value:
        value = root
        for key in keys:
            value = value.get(key) if isinstance(value, dict) else None
        if isinstance(value, int) and not isinstance(value, bool):
            return float(value)
        if value is None or isinstance(value, str | float | bool | dict | list):
            return value
        raise ExpressionError(f"unsupported context value for {name}")

    return run


class _Parser:
    """Recursive-descent parser from one token list to an unevaluated thunk."""

    def __init__(self, tokens: list[tuple[str, str]], context: dict[str, Value]):
        self._tokens = tokens
        self._index = 0
        self._context = context

    def parse(self) -> Thunk:
        """Parse the whole token list; trailing tokens are an error."""
        thunk = self._or()
        if self._index != len(self._tokens):
            raise ExpressionError(f"unexpected token: {self.peek()[1]!r}")
        return thunk

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

    def _or(self) -> Thunk:
        thunk = self._and()
        while self.accept("||"):
            thunk = _either(thunk, self._and())
        return thunk

    def _and(self) -> Thunk:
        thunk = self._equality()
        while self.accept("&&"):
            thunk = _both(thunk, self._equality())
        return thunk

    def _equality(self) -> Thunk:
        thunk = self._unary()
        while self.peek() in (("op", "=="), ("op", "!=")):
            negate = self.peek()[1] == "!="
            self._index += 1
            thunk = _compare(thunk, self._unary(), negate=negate)
        return thunk

    def _unary(self) -> Thunk:
        if self.accept("!"):
            return _negation(self._unary())
        return self._primary()

    def _primary(self) -> Thunk:
        kind, text = self.peek()
        self._index += 1
        if kind == "string":
            return _constant(text[1:-1].replace("''", "'"))
        if kind == "number":
            return _constant(float(text))
        if kind == "op" and text == "(":
            thunk = self._or()
            self.expect(")")
            return thunk
        if kind == "name":
            if text in _KEYWORDS:
                return _constant(_KEYWORDS[text])
            if self.accept("("):
                return self._call(text)
            return self._property(text)
        raise ExpressionError(f"unexpected token: {text!r}")

    def _call(self, name: str) -> Thunk:
        if name.lower() not in _FUNCTIONS:
            raise ExpressionError(f"unsupported function: {name}")
        function, minimum, maximum = _FUNCTIONS[name.lower()]
        args: list[Thunk] = []
        if not self.accept(")"):
            args.append(self._or())
            while self.accept(","):
                args.append(self._or())
            self.expect(")")
        if not minimum <= len(args) <= maximum:
            raise ExpressionError(f"wrong number of arguments for {name}")
        return _call(function, args)

    def _property(self, name: str) -> Thunk:
        if name not in self._context:
            raise ExpressionError(f"unknown context: {name}")
        keys: list[str] = []
        while self.accept("."):
            kind, key = self.peek()
            if kind != "name":
                raise ExpressionError(f"expected a property name after {name!r}")
            self._index += 1
            keys.append(key)
        return _lookup(self._context[name], keys, name)


def evaluate(expression: str, context: dict[str, Value]) -> Value:
    """Evaluate one expression (the text between ``${{`` and ``}}``).

    The whole expression is parsed first; evaluation then follows the
    operators, so a short-circuited operand is never evaluated.
    """
    tokens = _tokenize(expression)
    if not tokens:
        raise ExpressionError("empty expression")
    return _Parser(tokens, context).parse()()


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
