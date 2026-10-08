"""Tests for the send_card_offer_email MCP tool. SMTP is faked -- nothing is ever sent."""
from __future__ import annotations

import asyncio
import json
import smtplib

import pytest
from mcp.server.mcpserver.exceptions import ToolError
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import mcp_server.offer_email as offer_email
from api.repository.cards import encode_key_features, encode_relationship_discounts
from db.models import Account, Base, CardProduct, Customer, Customer360
from mcp_server.server import fetch_send_card_offer_email, mcp

ACCOUNT_NUMBER = "ACC101"
DISCOUNTS = [
    {"discount_type": ["joining fees"], "relationship_tier": 2, "value": "15%"},
    {"discount_type": ["joining fees"], "relationship_tier": 3, "value": "25%"},
]


class FakeSMTP:
    sent: list = []
    logins: list = []
    fail = False

    def __init__(self, host, port, timeout=None):
        self.host, self.port = host, port
        self.started_tls = False

    def __enter__(self):
        if FakeSMTP.fail:
            raise smtplib.SMTPConnectError(421, "service unavailable")
        return self

    def __exit__(self, *exc):
        return False

    def starttls(self, **kwargs):
        self.started_tls = True

    def login(self, user, password):
        assert self.started_tls, "must start TLS before logging in"
        FakeSMTP.logins.append((user, password))

    def send_message(self, message):
        assert self.started_tls
        FakeSMTP.sent.append(message)


@pytest.fixture(autouse=True)
def fake_mail(monkeypatch, tmp_path):
    FakeSMTP.sent, FakeSMTP.logins, FakeSMTP.fail = [], [], False
    monkeypatch.setattr(offer_email.smtplib, "SMTP", FakeSMTP)
    pdf = tmp_path / "card.pdf"
    pdf.write_bytes(b"%PDF-1.4 test brochure")
    monkeypatch.setattr(offer_email, "BROCHURE_DIR", tmp_path)
    for name, value in {
        "SMTP_HOST": "smtp.test",
        "SMTP_PORT": "587",
        "SMTP_USER": "mailer",
        "SMTP_PASSWORD": "secret",
        "MAIL_FROM": "rm@bank.test",
    }.items():
        monkeypatch.setenv(name, value)
    monkeypatch.delenv("MAIL_REDIRECT_TO", raising=False)


def _make_factory(tmp_path, tier=3, email_work="Singh.Vipul@bcg.com", email_personal="vipul@email.com", active=True):
    engine = create_engine(f"sqlite:///{tmp_path / 'email_test.db'}")
    Base.metadata.create_all(bind=engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as session:
        session.add(Account(account_number=ACCOUNT_NUMBER))
        customer = Customer(
            account_number=ACCOUNT_NUMBER,
            full_name="Vipul Singh",
            registered_email="work@email.com",
            relationship_tier="PRIORITY",
        )
        product = CardProduct(
            name="Global Elite zero forex markup credit card",
            network="Visa",
            card_type="credit",
            forex_markup_pct=0,
            joining_fee=15000,
            annual_fee=0,
            lounge_visits_domestic_per_year=12,
            lounge_visits_international_per_year=6,
            external_product_id="prod-2",
            is_active=active,
            key_features=encode_key_features(["Zero forex markup on international transactions"]),
            relationship_discounts_appl=encode_relationship_discounts(DISCOUNTS),
            eligibility_criteria=json.dumps(["Customer should be from a relationship tier of 2 or higher"]),
        )
        session.add_all([customer, product])
        session.flush()
        session.add(
            Customer360(
                customer_id=customer.id,
                account_number=ACCOUNT_NUMBER,
                customer_name="VIPUL SINGH",
                email_work=email_work,
                email_personal=email_personal,
                relationship_tier=tier,
            )
        )
        session.commit()
    return factory


def test_sends_email_with_discount_and_pdf(tmp_path):
    factory = _make_factory(tmp_path)

    result = fetch_send_card_offer_email(ACCOUNT_NUMBER, 1, "As discussed on our call.", session_factory=factory)

    assert result["status"] == "SENT"
    assert result["sent_to"] == "Singh.Vipul@bcg.com"
    assert result["discount_applied"] == "25% off joining fees"
    assert result["attachment"] == "card.pdf"
    (message,) = FakeSMTP.sent
    assert message["To"] == "Singh.Vipul@bcg.com"
    assert message["From"] == "rm@bank.test"
    body = message.get_body(preferencelist=("plain",)).get_content()
    assert "Dear VIPUL SINGH" in body
    assert "As discussed on our call." in body
    assert "Zero forex markup on international transactions" in body
    assert "INR 15,000.00" in body
    assert "25% off joining fees" in body
    assert "tier of 2 or higher" in body
    (attachment,) = list(message.iter_attachments())
    assert attachment.get_filename() == "card.pdf"
    assert attachment.get_content_type() == "application/pdf"
    assert FakeSMTP.logins == [("mailer", "secret")]


def test_tier_without_a_discount_gets_no_offer_line(tmp_path):
    factory = _make_factory(tmp_path, tier=1)

    result = fetch_send_card_offer_email(ACCOUNT_NUMBER, 1, session_factory=factory)

    assert result["discount_applied"] is None
    body = FakeSMTP.sent[0].get_body(preferencelist=("plain",)).get_content()
    assert "Special offer" not in body


def test_falls_back_to_personal_email(tmp_path):
    factory = _make_factory(tmp_path, email_work=None)

    result = fetch_send_card_offer_email(ACCOUNT_NUMBER, 1, session_factory=factory)

    assert result["sent_to"] == "vipul@email.com"


def test_mail_redirect_to_overrides_recipient(tmp_path, monkeypatch):
    monkeypatch.setenv("MAIL_REDIRECT_TO", "me@test.local")
    factory = _make_factory(tmp_path)

    result = fetch_send_card_offer_email(ACCOUNT_NUMBER, 1, session_factory=factory)

    assert result["sent_to"] == "me@test.local"
    assert FakeSMTP.sent[0]["To"] == "me@test.local"


def test_unknown_account_raises(tmp_path):
    factory = _make_factory(tmp_path)
    with pytest.raises(ToolError):
        fetch_send_card_offer_email("nope", 1, session_factory=factory)
    assert FakeSMTP.sent == []


def test_unknown_product_raises(tmp_path):
    factory = _make_factory(tmp_path)
    with pytest.raises(ToolError):
        fetch_send_card_offer_email(ACCOUNT_NUMBER, 999, session_factory=factory)
    assert FakeSMTP.sent == []


def test_inactive_product_raises(tmp_path):
    factory = _make_factory(tmp_path, active=False)
    with pytest.raises(ToolError, match="not currently offered"):
        fetch_send_card_offer_email(ACCOUNT_NUMBER, 1, session_factory=factory)
    assert FakeSMTP.sent == []


def test_no_email_on_file_raises(tmp_path):
    factory = _make_factory(tmp_path, email_work=None, email_personal=None)
    with pytest.raises(ToolError, match="No email address"):
        fetch_send_card_offer_email(ACCOUNT_NUMBER, 1, session_factory=factory)
    assert FakeSMTP.sent == []


def test_missing_pdf_raises_and_sends_nothing(tmp_path, monkeypatch):
    monkeypatch.setattr(offer_email, "BROCHURE_DIR", tmp_path / "nowhere")
    factory = _make_factory(tmp_path)
    with pytest.raises(ToolError, match="brochure"):
        fetch_send_card_offer_email(ACCOUNT_NUMBER, 1, session_factory=factory)
    assert FakeSMTP.sent == []


def test_missing_smtp_config_raises(tmp_path, monkeypatch):
    monkeypatch.delenv("SMTP_HOST")
    factory = _make_factory(tmp_path)
    with pytest.raises(ToolError, match="not configured"):
        fetch_send_card_offer_email(ACCOUNT_NUMBER, 1, session_factory=factory)
    assert FakeSMTP.sent == []


def test_smtp_failure_raises_tool_error(tmp_path):
    FakeSMTP.fail = True
    factory = _make_factory(tmp_path)
    with pytest.raises(ToolError, match="Could not send"):
        fetch_send_card_offer_email(ACCOUNT_NUMBER, 1, session_factory=factory)


def test_tool_is_registered_and_warns_it_sends_immediately():
    tools = {t.name: t for t in asyncio.run(mcp.list_tools())}
    assert "send_card_offer_email" in tools
    assert "no preview or approval step" in tools["send_card_offer_email"].description


def test_product_without_a_brochure_raises_and_sends_nothing(tmp_path, monkeypatch):
    monkeypatch.setattr(offer_email, "BROCHURE_BY_PRODUCT", {})
    factory = _make_factory(tmp_path)
    with pytest.raises(ToolError, match="No brochure"):
        fetch_send_card_offer_email(ACCOUNT_NUMBER, 1, session_factory=factory)
    assert FakeSMTP.sent == []
