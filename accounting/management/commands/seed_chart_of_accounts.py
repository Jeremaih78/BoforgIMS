from django.core.management.base import BaseCommand
from decimal import Decimal

from accounting.models import Account, Currency, TaxRate, BankAccount


class Command(BaseCommand):
    help = "Seed a standard SME Chart of Accounts, base currency, and default tax"

    def handle(self, *args, **options):
        # Currency
        usd, _ = Currency.objects.get_or_create(code="USD", defaults={"name": "US Dollar", "symbol": "$", "is_base": True})

        # Accounts
        accounts = [
            ("1000", "Cash", Account.ASSET, None),
            ("1100", "Bank USD", Account.ASSET, None),
            ("1110", "Bank ZWL", Account.ASSET, None),
            ("1200", "Accounts Receivable", Account.ASSET, None),
            ("1300", "Inventory", Account.ASSET, None),
            ("1400", "Prepaid", Account.ASSET, None),
            ("1500", "Fixed Assets", Account.ASSET, None),
            ("1600", "Accumulated Depreciation", Account.ASSET, None),
            ("2000", "Accounts Payable", Account.LIABILITY, None),
            ("2100", "Tax/VAT Payable", Account.LIABILITY, None),
            ("2200", "Payroll Liabilities", Account.LIABILITY, None),
            ("2300", "Loans", Account.LIABILITY, None),
            ("3000", "Owner's Equity", Account.EQUITY, None),
            ("3100", "Retained Earnings", Account.EQUITY, None),
            ("4000", "Sales", Account.REVENUE, None),
            ("4100", "Other Income", Account.REVENUE, None),
            ("5000", "COGS", Account.EXPENSE, None),
            ("5100", "Operating Expenses", Account.EXPENSE, None),
            ("5200", "Bank Charges", Account.EXPENSE, None),
            ("5300", "Utilities", Account.EXPENSE, None),
            ("5400", "Marketing", Account.EXPENSE, None),
            ("5500", "Repairs", Account.EXPENSE, None),
            ("5600", "Delivery", Account.EXPENSE, None),
        ]
        existing = {a.code for a in Account.objects.all()}
        for code, name, typ, parent in accounts:
            if code in existing:
                continue
            Account.objects.create(code=code, name=name, type=typ, parent=parent)

        # Default bank
        bank_gl = Account.objects.get(code="1100")
        BankAccount.objects.get_or_create(name="Default Bank", account=bank_gl, currency=usd)

        # Default Tax 15%
        TaxRate.objects.get_or_create(name="VAT 15%", defaults={"rate": Decimal("15.00"), "is_default": True})

        self.stdout.write(self.style.SUCCESS("Seed complete."))

