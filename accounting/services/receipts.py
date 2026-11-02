from decimal import Decimal
from django.conf import settings
from django.core.exceptions import ValidationError
from accounting.models import Currency, JournalEntry
from .money import book_rate, resolve_bank


def invoice_currency_code(invoice):
    codes=set(invoice.lines.exclude(product=None).values_list('product__currency',flat=True))
    if len(codes)>1:
        raise ValidationError('This invoice contains multiple currencies. Correct its currency before recording a payment.')
    return next(iter(codes),getattr(settings,'BASE_CURRENCY_CODE','USD'))


def invoice_receipt_setup(invoice, bank=None, fx_rate=None, lock=True):
    code=invoice_currency_code(invoice)
    currency=Currency.objects.filter(company=None,code=code).order_by('pk').first()
    if currency is None:
        raise ValidationError(f'Create a {code} currency and matching money account before recording this receipt.')
    previous=JournalEntry.objects.filter(source='INVOICE',source_id=invoice.pk,is_posted=True,lines__account__code='4000').first()
    if fx_rate is None:
        fx_rate=previous.fx_rate if previous else (Decimal('1') if code==getattr(settings,'BASE_CURRENCY_CODE','USD') else None)
    if fx_rate is None:
        raise ValidationError('Enter the agreed invoice book exchange rate for this foreign-currency receipt.')
    rate=book_rate(currency,fx_rate)
    if previous and (previous.currency_id!=currency.pk or previous.fx_rate!=rate):
        raise ValidationError('Use this invoice’s recorded currency and book exchange rate. Currency conversions must be recorded separately.')
    return currency,resolve_bank(currency,bank,lock=lock),rate
