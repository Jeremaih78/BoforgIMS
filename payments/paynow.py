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


class BoundedPaynowClient(PaynowClient):
    """Keep the pinned 1.0.8 SDK signing/parser, but bound its HTTP request.

    The SDK has no injectable transport or timeout. Keep private SDK access
    isolated here and cover it with a contract test before upgrading Paynow.
    """
    def send(self, payment):
        from urllib.parse import parse_qs
        from paynow.model import InitResponse
        if payment.total() <= 0:
            raise ValueError('Payment total must be positive.')
        data = self._Paynow__build(payment)
        response = requests.post(self.URL_INITIATE_TRANSACTION, data=data,
                                 timeout=(5, 15), allow_redirects=False)
        response.raise_for_status()
        payload = self._Paynow__rebuild_response(parse_qs(response.text))
        if payload.get('status', '').lower() != 'error':
            if not self._Paynow__verify_hash(payload, self.integration_key):
                raise HashMismatchException('Invalid provider response signature.')
        return InitResponse(payload)


def _build_client(return_url: str = '', result_url: str = '') -> PaynowClient:
    return BoundedPaynowClient(
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
        logger.warning('Paynow initiation failed (%s).', type(exc).__name__)
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
        logger.warning('Paynow initiation returned a non-success status.')

    return result


def poll_status(poll_url: str):
    """Use the stored provider URL over verified HTTPS, with bounded network time."""
    from urllib.parse import urlsplit, parse_qsl

    parsed = urlsplit(poll_url or '')
    if parsed.scheme != 'https' or parsed.hostname not in {'www.paynow.co.zw', 'paynow.co.zw'} or parsed.port not in (None, 443) or parsed.username:
        return {'status': 'unknown', 'raw': {}}
    try:
        response = requests.post(poll_url, data={}, timeout=(5, 15), allow_redirects=False)
        response.raise_for_status()
        raw = dict(parse_qsl(response.text))
        return {'status': raw.get('status', 'unknown').lower(), 'raw': raw}
    except (requests.RequestException, ValueError):
        logger.warning('Paynow status verification unavailable.')
        return {'status': 'unknown', 'raw': {}}
