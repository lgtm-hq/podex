"""Provider model names that the model-name columns can store.

``semantic_chunks.embedding_model``, ``episode_summaries.source_model``,
``media_summaries.source_model`` and ``derivative_generation_runs.summary_model``
are all ``String(MODEL_NAME_LENGTH)``. Each provider's name is checked against
this one rule before the provider is called.
"""

from __future__ import annotations

import unicodedata

MODEL_NAME_LENGTH = 120
"""Length of every provider model-name column, in characters."""

# Characters no model name may hold: control characters (NUL, which
# PostgreSQL text refuses, included) and lone surrogates, which no database
# driver can encode.
_UNSTORABLE_NAME_CATEGORIES = frozenset({"Cc", "Cs"})


def exact_str(value: object) -> str | None:
    """Return ``value`` as an exact built-in ``str``, or ``None`` if it is not one.

    A ``str`` subclass is copied by ``str.__str__``, which reads its characters
    without calling any method the subclass defines, so later checks and the
    stored value see what it holds. ``isinstance`` may still run ``value``'s
    own ``__class__`` lookup, so callers reading a provider's value run this
    inside their provider guard.

    Args:
        value: Value that should be a string.

    Returns:
        The characters of ``value`` as an exact ``str``, or ``None``.
    """
    if isinstance(value, str):
        return str.__str__(value)
    return None


def is_storable_model_name(value: str) -> bool:
    """Return whether a model-name column can store ``value`` on every dialect.

    SQLite would store an over-long name that PostgreSQL refuses, so the
    check is the same everywhere. Only control characters and lone surrogates
    are refused: format, private-use and unassigned code points are stored as
    given. The check reads an exact copy of ``value``, so a ``str`` subclass's
    own methods cannot misreport it; store ``exact_str(value)``, not ``value``.

    Args:
        value: Model name to store.

    Returns:
        ``True`` for a name that is not empty or only whitespace, has at most
        ``MODEL_NAME_LENGTH`` characters, and has no control character or
        lone surrogate.
    """
    name = str.__str__(value)
    return (
        bool(name.strip())
        and len(name) <= MODEL_NAME_LENGTH
        and not any(
            unicodedata.category(character) in _UNSTORABLE_NAME_CATEGORIES
            for character in name
        )
    )
