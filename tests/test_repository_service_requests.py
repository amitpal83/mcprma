"""Tests for api.repository.service_requests."""
from __future__ import annotations

from datetime import date

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from api.repository.service_requests import (
    ServiceRequestNotFoundError,
    get_latest_service_request,
)
from db.models import Account, Base, Customer, ServiceRequest

ACCOUNT_NUMBER = "8552"


@pytest.fixture
def session_factory(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'test.db'}")
    Base.metadata.create_all(bind=engine)
    return sessionmaker(bind=engine, expire_on_commit=False)


@pytest.fixture
def seeded_session_factory(session_factory):
    with session_factory() as session:
        session.add(Account(account_number=ACCOUNT_NUMBER))
        customer = Customer(
            account_number=ACCOUNT_NUMBER,
            full_name="Vipul Singh",
            registered_email="work@email.com",
            relationship_tier="PRIORITY",
        )
        session.add(customer)
        session.flush()

        session.add(
            ServiceRequest(
                customer_id=customer.id,
                service_request_id="SR-OLD-20260101",
                service_request_type="card_replacement",
                service_request_date=date(2026, 1, 1),
                service_request_status="closed",
                service_request_delivery_address_type="HOME",
            )
        )
        session.add(
            ServiceRequest(
                customer_id=customer.id,
                service_request_id="SR1156788-20261001",
                service_request_type="account_statement",
                service_request_date=date(2026, 10, 1),
                service_request_status="under progress",
                service_request_details="Dispatched via courier, expected delivery by 2026-10-07",
                service_request_delivery_address_type="Bank Branch",
            )
        )
        session.commit()
        customer_id = customer.id

    return session_factory, customer_id


def test_get_latest_service_request_returns_most_recent_by_date(seeded_session_factory):
    factory, customer_id = seeded_session_factory
    with factory() as session:
        latest = get_latest_service_request(session, customer_id)
        assert latest.service_request_id == "SR1156788-20261001"
        assert latest.service_request_status == "under progress"


def test_get_latest_service_request_unknown_customer_raises(seeded_session_factory):
    factory, _ = seeded_session_factory
    with factory() as session:
        with pytest.raises(ServiceRequestNotFoundError):
            get_latest_service_request(session, 99999)
