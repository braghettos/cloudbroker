"""Shared API utilities."""


def parse_id_or_slug(value: str | None) -> int | str | None:
    """Return id (int) or slug (str) for filtering. None passthrough."""
    if value is None:
        return None
    if value.isdigit():
        return int(value)
    return value
