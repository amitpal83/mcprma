"""Framework-agnostic data-access layer, split by domain.

Re-exports everything from the domain submodules so existing callers
(api/main.py, mcp_server/server.py) can keep doing
`from api.repository import X` unchanged as this package grows.
"""
from __future__ import annotations

from api.repository.cards import (
    CardNotFoundError,
    CardProductNotFoundError,
    CardRecommendation,
    CategorySpend,
    ForexSummary,
    NoEligibleCardProductError,
    decode_eligibility_criteria,
    decode_key_features,
    decode_reward_transfer_partners,
    encode_eligibility_criteria,
    encode_key_features,
    encode_reward_transfer_partners,
    get_card,
    get_card_category_breakdown,
    get_card_forex_summary,
    get_card_product,
    list_card_products,
    list_cards_for_account,
    recommend_card_upgrade,
    search_card_transactions,
)
from api.repository.customers import (
    CustomerNotFoundError,
    InvalidDeliveryAddressTypeError,
    get_customer_by_account,
    mask_email,
    update_customer_delivery_preference,
)
from api.repository.applications import (
    CardApplicationNotFoundError,
    DuplicateApplicationError,
    create_card_application,
    get_card_application_status,
)
from api.repository.disputes import (
    DisputeAlreadyOpenError,
    DisputeNotFoundError,
    DisputeNotOpenError,
    TransactionNotFoundError,
    create_dispute,
    withdraw_dispute,
)
from api.repository.merchants import (
    FUZZY_MATCH_THRESHOLD,
    MerchantNotResolvedError,
    resolve_merchant,
)
from api.repository.transactions import (
    AccountNotFoundError,
    InvalidDateRangeError,
    get_account_txn_details,
)
from api.repository.service_requests import (
    ServiceRequestNotFoundError,
    get_latest_service_request,
)
from api.repository.customer_360 import (
    Customer360NotFoundError,
    get_customer_360,
)

__all__ = [
    "AccountNotFoundError",
    "InvalidDateRangeError",
    "get_account_txn_details",
    "FUZZY_MATCH_THRESHOLD",
    "MerchantNotResolvedError",
    "resolve_merchant",
    "CardNotFoundError",
    "CardProductNotFoundError",
    "CardRecommendation",
    "CategorySpend",
    "ForexSummary",
    "NoEligibleCardProductError",
    "decode_eligibility_criteria",
    "decode_key_features",
    "decode_reward_transfer_partners",
    "encode_eligibility_criteria",
    "encode_key_features",
    "encode_reward_transfer_partners",
    "get_card",
    "get_card_forex_summary",
    "get_card_product",
    "get_card_category_breakdown",
    "list_card_products",
    "list_cards_for_account",
    "recommend_card_upgrade",
    "search_card_transactions",
    "CustomerNotFoundError",
    "InvalidDeliveryAddressTypeError",
    "get_customer_by_account",
    "mask_email",
    "update_customer_delivery_preference",
    "DisputeAlreadyOpenError",
    "DisputeNotFoundError",
    "DisputeNotOpenError",
    "TransactionNotFoundError",
    "create_dispute",
    "withdraw_dispute",
    "CardApplicationNotFoundError",
    "DuplicateApplicationError",
    "create_card_application",
    "get_card_application_status",
    "ServiceRequestNotFoundError",
    "get_latest_service_request",
    "Customer360NotFoundError",
    "get_customer_360",
]
