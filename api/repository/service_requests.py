"""Service request lookups.

Framework-agnostic, following the same pattern as api/repository/customers.py:
the REST API and MCP server both call get_latest_service_request() directly.
"""
from __future__ import annotations

import logging

from sqlalchemy.orm import Session

from db.models import ServiceRequest

logger = logging.getLogger(__name__)


class ServiceRequestNotFoundError(Exception):
    def __init__(self, customer_id: int) -> None:
        self.customer_id = customer_id
        super().__init__(f"No service request found for customer: {customer_id}")


def get_latest_service_request(session: Session, customer_id: int) -> ServiceRequest:
    """Return the most recent service request for customer_id (by service_request_date).

    Raises:
        ServiceRequestNotFoundError: if customer_id has no service requests.
    """
    request = (
        session.query(ServiceRequest)
        .filter(ServiceRequest.customer_id == customer_id)
        .order_by(ServiceRequest.service_request_date.desc(), ServiceRequest.id.desc())
        .first()
    )
    if request is None:
        raise ServiceRequestNotFoundError(customer_id)

    logger.info("get_latest_service_request: customer_id=%s -> %s", customer_id, request.service_request_id)
    return request
