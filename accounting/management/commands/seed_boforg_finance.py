from django.core.management.base import BaseCommand
from decimal import Decimal

from accounting.models import Company, Currency, TaxRate, Account, ExpenseCategory, BankAccount


SEED_ACCOUNTS = [
    ("1000", "Cash", Account.ASSET),
    ("1100", "Bank USD", Account.ASSET),
    ("1110", "Bank ZWL", Account.ASSET),
    ("1200", "Accounts Receivable", Account.ASSET),
    ("1300", "Inventory", Account.ASSET),
    ("2000", "Accounts Payable", Account.LIABILITY),
    ("2100", "VAT Payable", Account.LIABILITY),
    ("3000", "Owner's Equity", Account.EQUITY),
    ("3100", "Retained Earnings", Account.EQUITY),
    ("4000", "Sales", Account.REVENUE),
    ("5000", "COGS", Account.EXPENSE),
    ("5100", "Operating Expenses", Account.EXPENSE),
    ("5200", "Bank Charges", Account.EXPENSE),
    ("5300", "Utilities", Account.EXPENSE),
    ("5400", "Marketing", Account.EXPENSE),
    ("5500", "Repairs", Account.EXPENSE),
    ("5600", "Delivery", Account.EXPENSE),
    ("1410", "VAT Input", Account.ASSET),
]


CATEGORY_SEED = [
    { 'name': "Inventory – Resale (Printing & Heat Presses)", 'default_account_code': "5000" },
    { 'name': "Spare Parts & Consumables",                'default_account_code': "5000" },
    { 'name': "Packaging & Labels",                       'default_account_code': "5000" },
    { 'name': "Freight, Duty & Clearing (COGS)",          'default_account_code': "5000" },
    { 'name': "Warranty Parts & Claims (COGS)",           'default_account_code': "5000" },
    { 'name': "Inventory Write-offs & Shrinkage (COGS)",  'default_account_code': "5000" },

    { 'name': "Courier & Local Deliveries",               'default_account_code': "5600" },
    { 'name': "Fuel",                                     'default_account_code': "5100" },
    { 'name': "Vehicle Maintenance & Licensing",          'default_account_code': "5500" },
    { 'name': "Equipment Rental & Tooling",               'default_account_code': "5100" },

    { 'name': "Premises Rent",                            'default_account_code': "5100" },
    { 'name': "Electricity & Power",                      'default_account_code': "5300" },
    { 'name': "Water & Sanitation",                       'default_account_code': "5300" },
    { 'name': "Security Services",                        'default_account_code': "5100" },
    { 'name': "Cleaning & Hygiene",                       'default_account_code': "5100" },
    { 'name': "Premises Repairs & Maintenance",           'default_account_code': "5500" },

    { 'name': "Salaries & Wages",                         'default_account_code': "5100" },
    { 'name': "Overtime & Allowances",                    'default_account_code': "5100" },
    { 'name': "Employer Social Contributions",            'default_account_code': "5100" },
    { 'name': "Staff Training & Development",             'default_account_code': "5100" },
    { 'name': "Uniforms & PPE",                           'default_account_code': "5100" },

    { 'name': "Business Travel (Transport)",              'default_account_code': "5100" },
    { 'name': "Accommodation",                            'default_account_code': "5100" },
    { 'name': "Meals & Client Entertainment",             'default_account_code': "5100" },

    { 'name': "Sales Commissions",                        'default_account_code': "5100" },
    { 'name': "Advertising – Social",                     'default_account_code': "5400" },
    { 'name': "Advertising – Print/Outdoor",              'default_account_code': "5400" },
    { 'name': "Promotions, Samples & Demos",              'default_account_code': "5400" },
    { 'name': "Printing of Marketing Materials",          'default_account_code': "5400" },

    { 'name': "Website, Domains & Hosting",               'default_account_code': "5100" },
    { 'name': "Internet & Airtime",                       'default_account_code': "5300" },
    { 'name': "WhatsApp/Twilio & SMS Bundles",            'default_account_code': "5100" },
    { 'name': "AI/Cloud & SaaS Subscriptions",            'default_account_code': "5100" },
    { 'name': "Software Licenses & Renewals",             'default_account_code': "5100" },

    { 'name': "Bank Charges & Merchant Fees",             'default_account_code': "5200" },
    { 'name': "Accounting, Audit & Bookkeeping",          'default_account_code': "5100" },
    { 'name': "Legal & Professional Services",            'default_account_code': "5100" },
    { 'name': "Consulting & Contractors (Software/Tech)", 'default_account_code': "5100" },

    { 'name': "Office Stationery & Supplies",             'default_account_code': "5100" },
    { 'name': "Postage & Courier Admin",                  'default_account_code': "5100" },

    { 'name': "Municipal & Trading Licenses",             'default_account_code': "5100" },
    { 'name': "Standards/Certifications Fees",            'default_account_code': "5100" },
    { 'name': "Import Permits & Surtax",                  'default_account_code': "5100" },
    { 'name': "VAT Input Adjustments (Non-claimable)",    'default_account_code': "5100" },

    { 'name': "Insurance – Business & Stock",             'default_account_code': "5100" },
    { 'name': "Insurance – Vehicles & Transit",           'default_account_code': "5100" },

    { 'name': "FX Losses",                                'default_account_code': "5100" },
    { 'name': "Interest & Finance Charges",               'default_account_code': "5100" },

    { 'name': "Depreciation – Equipment",                 'default_account_code': "5100" },
    { 'name': "Amortisation – Software",                  'default_account_code': "5100" },

    { 'name': "IT Hardware (Non-capital) & Repairs",      'default_account_code': "5500" },
    { 'name': "After-Sales Installations (Non-billable)", 'default_account_code': "5100" },
    { 'name': "R&D & Prototyping (Software/Hardware)",    'default_account_code': "5100" },
]


class Command(BaseCommand):
    help = "Seed minimal CoA, tax rates, currencies, and Boforg-tailored expense categories for a company"

    def add_arguments(self, parser):
        parser.add_argument('--company', required=False, help='Company name (default: Boforg Technologies)')

    def handle(self, *args, **options):
        company_name = options.get('company') or 'Boforg Technologies'
        company, _ = Company.objects.get_or_create(name=company_name)

        # Currencies
        usd, _ = Currency.objects.get_or_create(company=company, code='USD', defaults={'name': 'US Dollar', 'symbol': '$', 'is_base': True})
        zwl, _ = Currency.objects.get_or_create(company=company, code='ZWL', defaults={'name': 'Zimbabwe Dollar', 'symbol': 'Z$', 'is_base': False})
        if not company.base_currency:
            company.base_currency = usd
            company.save(update_fields=['base_currency'])

        # Tax
        vat15, _ = TaxRate.objects.get_or_create(company=company, name='VAT 15%', defaults={'rate': Decimal('15.00'), 'is_default': True, 'is_claimable': True})

        # Accounts
        for code, name, typ in SEED_ACCOUNTS:
            Account.objects.get_or_create(company=company, code=code, defaults={'name': name, 'type': typ})

        # Default bank
        bank_gl = Account.objects.get(company=company, code='1100')
        BankAccount.objects.get_or_create(company=company, name='Default Bank', account=bank_gl, currency=usd)

        # Categories
        for row in CATEGORY_SEED:
            acc = Account.objects.get(company=company, code=row['default_account_code'][:4])
            ExpenseCategory.objects.get_or_create(company=company, name=row['name'], defaults={'default_account': acc, 'default_tax': vat15})

        self.stdout.write(self.style.SUCCESS(f"Seeded finance for company: {company.name}"))

