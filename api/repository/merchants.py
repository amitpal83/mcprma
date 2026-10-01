"""Merchant resolution: mapping a raw statement descriptor to a canonical merchant.

Card statements show an acquirer-submitted descriptor (e.g. "WISDOM PROPERTY
NL II"), which is often the merchant's legal entity name, not the brand the
customer recognizes (e.g. "DoubleTree by Hilton Amsterdam"). This module
resolves that mapping deterministically for the demo: exact alias match
first, then a simple fuzzy fallback -- no external enrichment vendor call.
"""
from __future__ import annotations

import logging
from difflib import SequenceMatcher

from sqlalchemy.orm import Session

from db.models import Merchant, MerchantAlias

logger = logging.getLogger(__name__)

# Below this similarity ratio, we don't guess -- an unresolved merchant is
# surfaced to the caller rather than silently mismatched.
FUZZY_MATCH_THRESHOLD = 0.6


class MerchantNotResolvedError(Exception):
    def __init__(self, raw_descriptor: str) -> None:
        self.raw_descriptor = raw_descriptor
        super().__init__(f"Could not resolve merchant for descriptor: {raw_descriptor!r}")


def _normalize(descriptor: str) -> str:
    return " ".join(descriptor.split()).upper()


def resolve_merchant(session: Session, raw_descriptor: str) -> Merchant:
    """Resolve a raw statement descriptor to its canonical Merchant.

    Tries an exact match (normalized) against merchant_aliases.raw_pattern
    first; falls back to fuzzy string similarity against the same aliases
    if no exact match exists. Ties are broken by highest ratio, then lowest
    alias id (first one seeded wins).

    Raises:
        MerchantNotResolvedError: if no alias clears FUZZY_MATCH_THRESHOLD.
    """
    normalized = _normalize(raw_descriptor)

    exact = session.query(MerchantAlias).filter(MerchantAlias.raw_pattern == normalized).first()
    if exact is not None:
        logger.info("resolve_merchant: exact match for %r -> merchant_id=%s", raw_descriptor, exact.merchant_id)
        return session.get(Merchant, exact.merchant_id)

    best_alias: MerchantAlias | None = None
    best_ratio = 0.0
    for alias in session.query(MerchantAlias).order_by(MerchantAlias.id).all():
        ratio = SequenceMatcher(None, normalized, alias.raw_pattern).ratio()
        if ratio > best_ratio:
            best_ratio = ratio
            best_alias = alias

    if best_alias is not None and best_ratio >= FUZZY_MATCH_THRESHOLD:
        logger.info(
            "resolve_merchant: fuzzy match for %r -> merchant_id=%s (ratio=%.2f)",
            raw_descriptor,
            best_alias.merchant_id,
            best_ratio,
        )
        return session.get(Merchant, best_alias.merchant_id)

    logger.info("resolve_merchant: no match for %r (best_ratio=%.2f)", raw_descriptor, best_ratio)
    raise MerchantNotResolvedError(raw_descriptor)
