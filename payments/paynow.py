from __future__ import annotations

import logging
import os
from decimal import Decimal, InvalidOperation
from typing import Iterable, Sequence, Tuple

import requests
from paynow import HashMismatchException, Paynow as PaynowClient

logger = logging.getLogger(__name__)

PAYNOW_INTEGRATION_ID = os.getenv('PAYNOW_INTEGRATION_ID')
PAYNOW_INTEGRATION_KEY = os.getenv('PAYNOW_INTEGRATION_KEY')


def _coerce_amount(value) -> float:
    if isinstance(value, Decimal):
        return float(value.quantize(Decimal('0.01')))
    try:
        return float(Decimal(str(value)).quantize(Decimal('0.01')))
    except (InvalidOperation, TypeError, ValueError):
        return 0.0


def _build_client(return_url: str = '', result_url: str = '') -> PaynowClient:
    return PaynowClient(
        PAYNOW_INTEGRATION_ID or '',
        PAYNOW_INTEGRATION_KEY or '',
        return_url,
        result_url,
    )


def _normalize_items(items: Iterable[Tuple[str, Decimal]] | None) -> Sequence[Tuple[str, float]]:
    normalized = []
    if not items:
        return normalized
    for title, amount in items:
        normalized.append((title or 'Item', _coerce_amount(amount)))
    return normalized


def create_payment(
    *,
    order_number: str,
    email: str,
    amount,
    return_url: str,
    result_url: str,
    items: Iterable[Tuple[str, Decimal]] | None = None,
):
    if not PAYNOW_INTEGRATION_ID or not PAYNOW_INTEGRATION_KEY:
        logger.error('Paynow credentials are not configured.')
        return {'ok': False, 'error': 'Paynow credentials missing', 'raw': {}}

    client = _build_client(return_url, result_url)
    payment = client.create_payment(order_number, email or '')

    normalized_items = _normalize_items(items)
    if normalized_items:
        for title, line_total in normalized_items:
            payment.add(title, line_total)
    else:
        payment.add(f'Order {order_number}', _coerce_amount(amount))

    try:
        response = client.send(payment)
    except (HashMismatchException, requests.RequestException, ValueError) as exc:
        logger.exception('Error calling Paynow: %s', exc)
        return {'ok': False, 'error': str(exc), 'raw': {}}

    raw = getattr(response, 'data', {})
    result = {
        'ok': bool(getattr(response, 'success', False)),
        'redirect_url': getattr(response, 'redirect_url', ''),
        'poll_url': getattr(response, 'poll_url', ''),
        'reference': raw.get('reference', order_number),
        'raw': raw,
    }

    if not result['ok']:
        result['error'] = getattr(response, 'error', 'Paynow request failed')
        logger.error('Paynow initiate returned non-ok status: %s', raw)

    return result


def poll_status(poll_url: str):
    if not poll_url:
        return {'status': 'unknown', 'raw': {}}

    client = _build_client()

    try:
        response = client.check_transaction_status(poll_url)
    except requests.RequestException as exc:
        logger.exception('Error polling Paynow: %s', exc)
        return {'status': 'unknown', 'raw': {'error': str(exc)}}

    raw = {key: value for key, value in response.__dict__.items()}
    return {'status': raw.get('status', 'unknown'), 'raw': raw}
