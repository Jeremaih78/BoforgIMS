from django.db import migrations
from decimal import Decimal


def seed_defaults(apps, schema_editor):
    Currency = apps.get_model('accounting', 'Currency')
    Account = apps.get_model('accounting', 'Account')
    BankAccount = apps.get_model('accounting', 'BankAccount')
    TaxRate = apps.get_model('accounting', 'TaxRate')

    usd, _ = Currency.objects.get_or_create(code='USD', defaults={'name': 'US Dollar', 'symbol': '$', 'is_base': True})
    accounts = [
        ("1000", "Cash", 'ASSET'),
        ("1100", "Bank USD", 'ASSET'),
        ("1110", "Bank ZWL", 'ASSET'),
        ("1200", "Accounts Receivable", 'ASSET'),
        ("1300", "Inventory", 'ASSET'),
        ("1400", "Prepaid", 'ASSET'),
        ("1500", "Fixed Assets", 'ASSET'),
        ("1600", "Accumulated Depreciation", 'ASSET'),
        ("2000", "Accounts Payable", 'LIABILITY'),
        ("2100", "Tax/VAT Payable", 'LIABILITY'),
        ("2200", "Payroll Liabilities", 'LIABILITY'),
        ("2300", "Loans", 'LIABILITY'),
        ("3000", "Owner's Equity", 'EQUITY'),
        ("3100", "Retained Earnings", 'EQUITY'),
        ("4000", "Sales", 'REVENUE'),
        ("4100", "Other Income", 'REVENUE'),
        ("5000", "COGS", 'EXPENSE'),
        ("5100", "Operating Expenses", 'EXPENSE'),
        ("5200", "Bank Charges", 'EXPENSE'),
        ("5300", "Utilities", 'EXPENSE'),
        ("5400", "Marketing", 'EXPENSE'),
        ("5500", "Repairs", 'EXPENSE'),
        ("5600", "Delivery", 'EXPENSE'),
    ]
    existing = {a.code for a in Account.objects.all()}
    for code, name, typ in accounts:
        if code not in existing:
            Account.objects.create(code=code, name=name, type=typ)
    bank_gl = Account.objects.get(code='1100')
    BankAccount.objects.get_or_create(name='Default Bank', account=bank_gl, currency=usd)
    TaxRate.objects.get_or_create(name='VAT 15%', defaults={'rate': Decimal('15.00'), 'is_default': True})


def unseed_defaults(apps, schema_editor):
    # Keep seeded data; do not delete on reverse
    pass


class Migration(migrations.Migration):
    dependencies = [
        ('accounting', '0001_initial'),
    ]

    operations = [
        migrations.RunPython(seed_defaults, unseed_defaults),
    ]

