import hashlib
import logging
import os
from decimal import Decimal
from urllib.parse import parse_qs

import requests

logger = logging.getLogger(__name__)

PAYNOW_INTEGRATION_ID = os.getenv('PAYNOW_INTEGRATION_ID')
PAYNOW_INTEGRATION_KEY = os.getenv('PAYNOW_INTEGRATION_KEY')
PAYNOW_BASE = os.getenv('PAYNOW_BASE', 'https://www.paynow.co.zw')
PAYNOW_TIMEOUT = int(os.getenv('PAYNOW_TIMEOUT', '20'))

INITIATE_PATH = '/interface/initiatetransaction'


def _format_amount(amount) -> str:
    if isinstance(amount, Decimal):
        value = amount
    else:
        value = Decimal(str(amount))
    return f"{value.quantize(Decimal('0.01'))}"


def _build_payload(order_number: str, email: str, amount: Decimal, return_url: str, result_url: str):
    payload = {
        'id': PAYNOW_INTEGRATION_ID,
        'reference': order_number,
        'amount': _format_amount(amount),
        'returnurl': return_url,
        'resulturl': result_url,
        'authemail': email,
        'additionalinfo': order_number,
    }
    payload_str = '&'.join(f"{key}={value}" for key, value in payload.items())
    payload['hash'] = hashlib.md5((payload_str + (PAYNOW_INTEGRATION_KEY or '')).encode('utf-8')).hexdigest().upper()
    return payload


def _parse_response(text: str):
    data = {k: v[0] for k, v in parse_qs(text).items()}
    return data


def create_payment(*, order_number: str, email: str, amount, return_url: str, result_url: str):
    if not PAYNOW_INTEGRATION_ID or not PAYNOW_INTEGRATION_KEY:
        logger.error('Paynow credentials are not configured.')
        return {'ok': False, 'error': 'Paynow credentials missing', 'raw': {}}

    payload = _build_payload(order_number, email, amount, return_url, result_url)
    url = PAYNOW_BASE.rstrip('/') + INITIATE_PATH

    try:
        response = requests.post(url, data=payload, timeout=PAYNOW_TIMEOUT)
        response.raise_for_status()
    except requests.RequestException as exc:
        logger.exception('Error calling Paynow: %s', exc)
        return {'ok': False, 'error': str(exc), 'raw': {}}

    data = _parse_response(response.text)
    status = data.get('status', '').lower()
    ok = status == 'ok'

    result = {
        'ok': ok,
        'redirect_url': data.get('browserurl'),
        'poll_url': data.get('pollurl'),
        'reference': data.get('reference', order_number),
        'raw': data,
    }
    if not ok:
        logger.error('Paynow initiate returned non-ok status: %s', data)
    return result


def poll_status(poll_url: str):
    if not poll_url:
        return {'status': 'unknown', 'raw': {}}
    try:
        response = requests.get(poll_url, timeout=PAYNOW_TIMEOUT)
        response.raise_for_status()
    except requests.RequestException as exc:
        logger.exception('Error polling Paynow: %s', exc)
        return {'status': 'unknown', 'raw': {'error': str(exc)}}

    data = _parse_response(response.text)
    return {'status': data.get('status', 'unknown').lower(), 'raw': data}
