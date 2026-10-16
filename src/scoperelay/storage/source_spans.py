"""Validate citations into preserved lower-trust ticket text (DM-011, PRD-SEC018).

Offsets are Python Unicode code-point indices, not UTF-8 bytes or UTF-16 units.
This proves presence in one snapshot, never customer intent or authority.
"""

from hashlib import sha256


class SourceReferenceError(ValueError):
    """A source reference does not match its immutable snapshot."""


def source_digest(body_text: str) -> str:
    return sha256(body_text.encode("utf-8")).hexdigest()


def resolve_span(
    *,
    expected_ticket_id: str,
    expected_source_version: int,
    ticket_id: str,
    source_version: int,
    body_text: str,
    body_sha256: str,
    start: int,
    end: int,
    excerpt: str,
) -> str:
    if ticket_id != expected_ticket_id or source_version != expected_source_version:
        raise SourceReferenceError("wrong ticket or source version")
    if source_digest(body_text) != body_sha256:
        raise SourceReferenceError("source digest mismatch")
    if (
        type(start) is not int
        or type(end) is not int
        or not 0 <= start <= end <= len(body_text)
    ):
        raise SourceReferenceError("invalid code-point span")
    actual = body_text[start:end]
    if actual != excerpt:
        raise SourceReferenceError("excerpt does not match source span")
    return actual
