"""Tests for api.repository.merchants.resolve_merchant."""
from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from api.repository.merchants import MerchantNotResolvedError, resolve_merchant
from db.models import Base, Merchant, MerchantAlias


@pytest.fixture
def session_factory(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'test.db'}")
    Base.metadata.create_all(bind=engine)
    return sessionmaker(bind=engine, expire_on_commit=False)


@pytest.fixture
def seeded_session_factory(session_factory):
    with session_factory() as session:
        merchant = Merchant(
            brand_name="DoubleTree by Hilton Amsterdam Centraal Station",
            associated_property="DoubleTree by Hilton Amsterdam Centraal Station",
            mcc="7011",
            category="Travel",
            sub_category="Hotel",
            city="Amsterdam",
            country="NL",
        )
        session.add(merchant)
        session.flush()
        session.add(MerchantAlias(merchant_id=merchant.id, raw_pattern="WISDOM PROPERTY NL II"))
        session.commit()
    return session_factory


def test_exact_match_resolves(seeded_session_factory):
    with seeded_session_factory() as session:
        merchant = resolve_merchant(session, "WISDOM PROPERTY NL II")
        assert merchant.brand_name == "DoubleTree by Hilton Amsterdam Centraal Station"


def test_exact_match_is_whitespace_and_case_insensitive(seeded_session_factory):
    with seeded_session_factory() as session:
        merchant = resolve_merchant(session, "  wisdom   property nl ii  ")
        assert merchant.associated_property == "DoubleTree by Hilton Amsterdam Centraal Station"


def test_fuzzy_match_resolves_near_miss(seeded_session_factory):
    with seeded_session_factory() as session:
        merchant = resolve_merchant(session, "WISDOM PROPERTY NL 2")
        assert merchant.brand_name == "DoubleTree by Hilton Amsterdam Centraal Station"


def test_unrelated_descriptor_raises(seeded_session_factory):
    with seeded_session_factory() as session:
        with pytest.raises(MerchantNotResolvedError, match="STARBUCKS"):
            resolve_merchant(session, "STARBUCKS COFFEE #4821")
