"""Provider model names the model-name columns cannot store, and hostile ``str``s.

Shared by the embedding and summary model-name tests, which apply the same
rule (``podex.models.model_name``) to every ``String(120)`` model-name column.
"""

from collections.abc import Callable, Iterator
from dataclasses import dataclass

MODEL_COLUMN_LENGTH = 120


class DisguisedName(str):
    """A ``str`` whose length, stripped form and characters look harmless."""

    __slots__ = ()

    def strip(self, chars: str | None = None) -> str:
        """Return a harmless name instead of the stored characters."""
        return "disguised"

    def __len__(self) -> int:
        """Report a length within the column instead of the real one."""
        return len("disguised")

    def __iter__(self) -> Iterator[str]:
        """Yield harmless characters instead of the stored ones."""
        return iter("disguised")


class RaisingStripName(str):
    """A ``str`` whose ``strip`` raises."""

    __slots__ = ()

    def strip(self, chars: str | None = None) -> str:
        """Fail as a hostile override would."""
        raise RuntimeError("strip is not for callers")


@dataclass(frozen=True, slots=True)
class UnreadableName:
    """A model name whose every attribute read raises, ``__class__`` included.

    ``isinstance`` reads ``__class__`` of an object that is not a ``str``, so
    checking this name's type runs code that raises.
    """

    def __getattribute__(self, name: str) -> object:
        """Fail as a broken provider object would."""
        raise OSError("model name unavailable")


# Model names the columns cannot store, each built around a planted secret.
REJECTED_NAMES: dict[str, Callable[[str], object]] = {
    "none": lambda _secret: None,
    "list": lambda secret: [secret],
    "bytes": lambda secret: secret.encode(),
    "empty": lambda _secret: "",
    "whitespace-only": lambda _secret: "   ",
    "one-over-length": lambda secret: secret.ljust(MODEL_COLUMN_LENGTH + 1, "m"),
    "newline": lambda secret: f"{secret}\nmodel",
    "escape": lambda secret: f"{secret}\x1b[0m",
    "delete": lambda secret: f"{secret}\x7f",
    "c1-control": lambda secret: f"{secret}\x85",
    "nul": lambda secret: f"{secret}\x00",
    "lone-surrogate": lambda secret: f"{secret}\ud800",
    "disguised-subclass": lambda secret: DisguisedName(secret.ljust(200, "\x00")),
}
