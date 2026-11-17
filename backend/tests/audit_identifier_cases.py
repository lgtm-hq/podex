"""Audit identifiers replayed through revision 0024 on SQLite and PostgreSQL.

Downgrading 0024 narrows ``audit_logs.resource_identifier`` from 255 to 120
characters and cuts longer identifiers to their first 120 characters.
"""

# Two-, three- and four-byte UTF-8 characters: lengths count characters.
MULTIBYTE = "é漢🎧ß"
WIDE_LENGTH = 255
NARROW_LENGTH = 120


def multibyte(length: int) -> str:
    """Return ``length`` characters of mixed multi-byte text."""
    return (MULTIBYTE * length)[:length]


# Entries that fit the narrow column, stored before the upgrade.
NARROW_ENTRIES: dict[int, str | None] = {
    1: "short-slug",
    2: None,
    3: "x" * NARROW_LENGTH,
}
# Entries only the wide column holds, stored after the upgrade.
WIDE_ENTRIES: dict[int, str | None] = {
    4: "a" * WIDE_LENGTH,
    5: multibyte(WIDE_LENGTH),
}
# Every entry as it reads back after a downgrade.
DOWNGRADED_ENTRIES: list[tuple[int, str | None]] = [
    *NARROW_ENTRIES.items(),
    (4, "a" * NARROW_LENGTH),
    (5, multibyte(NARROW_LENGTH)),
]
