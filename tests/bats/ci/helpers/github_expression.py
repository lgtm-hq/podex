"""Evaluate GitHub Actions expressions for the workflow contract tests.

Implements the subset of the expression language that workflow concurrency
blocks use, with GitHub's semantics: literals (strings, numbers, true, false,
null), context property access, ``!``, ``==``, ``!=``, ``&&``, ``||``,
parentheses and the functions ``format``, ``startsWith``, ``endsWith`` and
``contains``. The rule throughout: anything the helper does not model is an
error, so it fails the test instead of producing a value.

An expression is parsed in full before it is evaluated: syntax, unknown
functions, wrong argument counts and unknown contexts are errors even in an
operand that is never evaluated. ``&&`` and ``||`` short-circuit and return an
operand, not a boolean: ``a && b`` is ``a`` when ``a`` is falsy and otherwise
``b``; ``a || b`` is ``a`` when ``a`` is truthy and otherwise ``b``. The falsy
values are ``false``, ``0``, ``NaN``, ``''`` and ``null``.

Context and property names match ignoring case. A property that the given
context does not define is an error naming it, raised when that operand is
evaluated (GitHub reads properties at run time). It is null only when its
parent is null or not an object, or when the context sets it to null.

String comparison (``==``, ``startsWith``, ...) ignores ASCII case; where only
non-ASCII case mapping would decide the result, it is an error. Operands of
different types compare as numbers: null is 0, booleans are 1 and 0, a string
is its JSON number (after trimming whitespace; empty is 0) or NaN, an object
or array is NaN, and NaN equals nothing.

Errors, because GitHub's result is not modelled or its documentation and
runner disagree: strings the runner reads as numbers beyond the JSON format
(``'+1'``, ``'.5'``, ``'007'``, ``'0x10'``, ``'0o7'``, ``'Infinity'``) compared
with a non-string; a numeric literal with leading zeros; a number rendered as
a string unless it is an integer below 1e15 in magnitude (GitHub uses .NET's
``G15`` format); an object or array used as a string (GitHub renders
``Object`` or ``Array``); a ``format`` string with a brace that is not part of
``{{``, ``}}`` or ``{N}``; ``format`` without a replacement argument.

Usage:
    github_expression.py render TEMPLATE CONTEXT_JSON
    github_expression.py truthy TEMPLATE CONTEXT_JSON

``render`` prints TEMPLATE with each ``${{ ... }}`` replaced by its value.
``truthy`` prints ``true`` or ``false`` for a value that GitHub reads as a
boolean, such as ``cancel-in-progress``: a single expression, or the literal
``true`` or ``false``. CONTEXT_JSON maps context names (``github``,
``inputs``, ...) to their values.

Exit status: 0 with the result on stdout; 3 with ``expression error: ...`` on
stderr for input outside the model; 2 for a usage error. Any other status is
a crash of the helper itself.
"""

from __future__ import annotations

import json
import math
import re
import sys
from collections.abc import Callable, Mapping

Value = str | float | bool | None | dict[str, object] | list[object]
Thunk = Callable[[], Value]

EXIT_EXPRESSION_ERROR = 3
EXIT_USAGE = 2

_TOKEN_RE = re.compile(
    r"""
    \s*(?:
      (?P<string>'(?:[^']|'')*')
    | (?P<number>[0-9]+(?:\.[0-9]+)?)
    | (?P<name>[A-Za-z_][A-Za-z0-9_-]*)
    | (?P<op>==|!=|&&|\|\||[!().,])
    )
    """,
    re.VERBOSE,
)
_FORMAT_INDEX_RE = re.compile(r"\{(0|[1-9][0-9]*)\}")
_JSON_NUMBER_RE = re.compile(r"-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?(?:[eE][+-]?[0-9]+)?")
# Read as numbers by the runner but not by the documented JSON number format.
_UNDOCUMENTED_NUMBER_RE = re.compile(
    r"[+-]?(?:[0-9]+\.?[0-9]*|\.[0-9]+)(?:[eE][+-]?[0-9]+)?|0x[0-9a-fA-F]+|0o[0-7]+"
    r"|[+-]?(?i:infinity)",
)
# Integers below this magnitude render the same in Python and in .NET's G15.
_PLAIN_INTEGER_LIMIT = 1e15
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
        kind, token = match.lastgroup, match.group(match.lastgroup)
        if kind == "number" and len(token.split(".")[0]) > 1 and token[0] == "0":
            raise ExpressionError(f"numeric literal with leading zeros: {token}")
        tokens.append((kind, token))
        position = match.end()
    return tokens


def _ascii_upper(text: str) -> str:
    return "".join(c.upper() if c.isascii() else c for c in text)


def _simple_upper(text: str) -> str:
    """Uppercase each character by its single-character (simple) mapping."""

    def one(c: str) -> str:
        for mapped in (c.upper(), c.title()):
            if len(mapped) == 1:
                return mapped
        return c

    return "".join(one(c) for c in text)


def _ignore_case(test: Callable[[str, str], bool], left: str, right: str) -> bool:
    """Apply TEST ignoring ASCII case; non-ASCII case must not decide it."""
    result = test(_ascii_upper(left), _ascii_upper(right))
    if result != test(_simple_upper(left), _simple_upper(right)):
        raise ExpressionError(
            f"non-ASCII case decides comparing {left!r} with {right!r}",
        )
    return result


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
        if value.is_integer() and abs(value) < _PLAIN_INTEGER_LIMIT:
            return str(int(value))
        raise ExpressionError(f"rendering the number {value!r} is not modelled")
    if isinstance(value, str):
        return value
    kind = "an object" if isinstance(value, dict) else "an array"
    raise ExpressionError(f"{kind} used as a string is not modelled")


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
        return _ignore_case(str.__eq__, left, right)
    if left is None and right is None:
        return True
    if isinstance(left, bool) and isinstance(right, bool):
        return left == right
    if isinstance(left, dict | list) or isinstance(right, dict | list):
        return left is right
    return _to_number(left) == _to_number(right)


def _format(template: Value, *args: Value) -> Value:
    text = _to_string(template)
    parts: list[str] = []
    position = 0
    while position < len(text):
        char = text[position]
        if char not in "{}":
            parts.append(char)
            position += 1
        elif text.startswith(char * 2, position):
            parts.append(char)
            position += 2
        elif match := _FORMAT_INDEX_RE.match(text, position):
            index = int(match.group(1))
            if index >= len(args):
                raise ExpressionError(f"format index {index} has no argument")
            parts.append(_to_string(args[index]))
            position = match.end()
        else:
            raise ExpressionError(f"invalid format string: {text!r}")
    return "".join(parts)


def _string_test(test: Callable[[str, str], bool]) -> Callable[..., Value]:
    def apply(left: Value, right: Value) -> Value:
        return _ignore_case(test, _to_string(left), _to_string(right))

    return apply


# Name (lower case) -> (implementation, minimum and maximum argument count).
_FUNCTIONS: dict[str, tuple[Callable[..., Value], int, float]] = {
    "format": (_format, 2, math.inf),
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


def _key(mapping: Mapping[str, object], name: str) -> str | None:
    """Return the key of MAPPING that matches NAME ignoring case, if any."""
    keys = [key for key in mapping if key.upper() == name.upper()]
    if len(keys) > 1:
        raise ExpressionError(f"{name} matches more than one key: {keys}")
    return keys[0] if keys else None


def _lookup(root: object, keys: list[str], name: str) -> Thunk:
    def run() -> Value:
        value = root
        path = name
        for key in keys:
            path = f"{path}.{key}"
            if not isinstance(value, dict):
                value = None
                continue
            found = _key(value, key)
            if found is None:
                raise ExpressionError(f"{path} is not in the test's context")
            value = value[found]
        if isinstance(value, int) and not isinstance(value, bool):
            return float(value)
        if value is None or isinstance(value, str | float | bool | dict | list):
            return value
        raise ExpressionError(f"unsupported context value for {path}")

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
        context_name = _key(self._context, name)
        if context_name is None:
            raise ExpressionError(f"unknown context: {name}")
        keys: list[str] = []
        while self.accept("."):
            kind, key = self.peek()
            if kind != "name":
                raise ExpressionError(f"expected a property name after {name!r}")
            self._index += 1
            keys.append(key)
        return _lookup(self._context[context_name], keys, name)


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
        return EXIT_USAGE
    context = json.loads(argv[3])
    if not isinstance(context, dict):
        print("CONTEXT_JSON must be an object", file=sys.stderr)
        return EXIT_USAGE
    try:
        if argv[1] == "render":
            print(render(argv[2], context))
        else:
            print("true" if truthy(argv[2], context) else "false")
    except ExpressionError as error:
        print(f"expression error: {error}", file=sys.stderr)
        return EXIT_EXPRESSION_ERROR
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
