from datetime import timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from accounting.models import (
    APPayment,
    ARPayment,
    Account,
    BankAccount,
    Company,
    Currency,
    Expense,
    ExpenseCategory,
    FiscalPeriod,
    SupplierBill,
    SupplierBillLine,
    TaxRate,
)
from credit_control.models import (
    CollectionTask,
    CreditorAccount,
    DebtorAccount,
    DebtorFollowUp,
    FollowUpTemplate,
    PromiseToPay,
    ReminderRule,
)
from customers.models import Customer
from inventory.models import (
    Category,
    Combo,
    ComboItem,
    Product,
    ProductUnit,
    Shipment,
    ShipmentCost,
    ShipmentItem,
    StockMovement,
    Supplier,
)
from sales.models import (
    DocumentLine,
    Invoice,
    Payment as SalesPayment,
    PriceRule,
    Quotation,
    StockReservation,
)
from shop.models import Order, OrderItem, Payment as ShopPayment


D = Decimal


class Command(BaseCommand):
    help = "Seed realistic, idempotent demo data across the IMS"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.created = 0

    def made(self, result):
        obj, created = result
        self.created += int(created)
        return obj

    @transaction.atomic
    def handle(self, *args, **options):
        self.today = timezone.localdate()
        self.actor = (
            get_user_model().objects.filter(is_superuser=True).first()
            or get_user_model().objects.first()
        )

        company, currency, vat, bank, accounts = self.seed_finance_setup()
        categories, suppliers, products = self.seed_inventory()
        customers = self.seed_customers()
        quotations, invoices = self.seed_sales(customers, products, categories)
        self.seed_shipments(suppliers, products)
        self.seed_shop(products)
        bills = self.seed_finance_activity(
            company, currency, vat, bank, accounts, suppliers, customers, products
        )
        self.seed_credit_control(company, customers, suppliers, invoices, bills)

        self.stdout.write(
            self.style.SUCCESS(
                f"Demo seed complete: {self.created} new records created. "
                "Run this command again safely; existing demo records are reused."
            )
        )

    def seed_finance_setup(self):
        company = self.made(
            Company.objects.get_or_create(
                name="Boforg Technologies Private Limited",
                defaults={"brand_primary_color": "#0f766e", "license_status": Company.VALID},
            )
        )

        # The accounting migration creates global defaults. Attach those records to
        # the demo company instead of duplicating account codes used by posting.py.
        Account.objects.filter(company__isnull=True).update(company=company)

        currency = Currency.objects.filter(code="USD").order_by("id").first()
        if currency is None:
            currency = Currency.objects.create(
                company=company, code="USD", name="US Dollar", symbol="$", is_base=True
            )
            self.created += 1
        elif currency.company_id is None:
            currency.company = company
            currency.name = "US Dollar"
            currency.symbol = "$"
            currency.is_base = True
            currency.save()

        if company.base_currency_id != currency.id:
            company.base_currency = currency
            company.save(update_fields=["base_currency"])

        vat = TaxRate.objects.filter(name="VAT 15%").order_by("id").first()
        if vat is None:
            vat = TaxRate.objects.create(
                company=company,
                name="VAT 15%",
                rate=D("15.00"),
                is_default=True,
                is_claimable=True,
            )
            self.created += 1
        elif vat.company_id is None:
            vat.company = company
            vat.save(update_fields=["company"])

        account_rows = [
            ("1000", "Cash", Account.ASSET),
            ("1100", "Bank USD", Account.ASSET),
            ("1200", "Accounts Receivable", Account.ASSET),
            ("1300", "Inventory", Account.ASSET),
            ("1410", "VAT Input", Account.ASSET),
            ("2000", "Accounts Payable", Account.LIABILITY),
            ("2100", "VAT Payable", Account.LIABILITY),
            ("3000", "Owner's Equity", Account.EQUITY),
            ("4000", "Sales", Account.REVENUE),
            ("5000", "Cost of Goods Sold", Account.EXPENSE),
            ("5100", "Operating Expenses", Account.EXPENSE),
            ("5200", "Bank Charges", Account.EXPENSE),
            ("5300", "Utilities", Account.EXPENSE),
            ("5400", "Marketing", Account.EXPENSE),
            ("5600", "Delivery", Account.EXPENSE),
        ]
        accounts = {}
        for code, name, account_type in account_rows:
            accounts[code] = self.made(
                Account.objects.get_or_create(
                    code=code,
                    defaults={"company": company, "name": name, "type": account_type},
                )
            )

        bank = BankAccount.objects.filter(name="Default Bank").order_by("id").first()
        if bank is None:
            bank = BankAccount.objects.create(
                company=company, name="Default Bank", account=accounts["1100"], currency=currency
            )
            self.created += 1
        elif bank.company_id is None:
            bank.company = company
            bank.save(update_fields=["company"])

        FiscalPeriod.objects.get_or_create(
            name=f"FY {self.today.year}",
            defaults={
                "company": company,
                "start_date": self.today.replace(month=1, day=1),
                "end_date": self.today.replace(month=12, day=31),
            },
        )
        return company, currency, vat, bank, accounts

    def seed_inventory(self):
        categories = {
            name: self.made(Category.objects.get_or_create(name=name))
            for name in [
                "Printers & Cutters",
                "Heat Presses",
                "Consumables",
                "Computers & Accessories",
                "Power Solutions",
            ]
        }

        supplier_rows = [
            ("Sign Africa Distribution", "sales@signafrica.co.za", "+27 11 555 0142", "Johannesburg, South Africa"),
            ("Shenzhen PrintTech", "exports@printtech.example", "+86 755 5550 8821", "Shenzhen, China"),
            ("Harare Office Supplies", "orders@hos.example", "+263 242 555 110", "Graniteside, Harare"),
            ("SolarTech Zimbabwe", "trade@solartech.example", "+263 8677 555 220", "Workington, Harare"),
        ]
        suppliers = {}
        for name, email, phone, address in supplier_rows:
            suppliers[name] = self.made(
                Supplier.objects.get_or_create(
                    name=name,
                    defaults={"email": email, "phone": phone, "address": address},
                )
            )

        product_rows = [
            ("BFG-EP-L3250", "Epson EcoTank L3250 Printer", "Printers & Cutters", "Sign Africa Distribution", "285.00", "205.00", 12, 3, Product.TRACK_SERIAL, "Wireless A4 EcoTank printer for home and small-office production."),
            ("BFG-CAMEO5", "Silhouette Cameo 5 Vinyl Cutter", "Printers & Cutters", "Sign Africa Distribution", "465.00", "348.00", 7, 2, Product.TRACK_SERIAL, "Precision desktop cutter for vinyl, card and heat-transfer media."),
            ("BFG-HP-38", "38 x 38 cm Heat Press", "Heat Presses", "Shenzhen PrintTech", "390.00", "270.00", 5, 2, Product.TRACK_SERIAL, "Digital clamshell heat press suitable for apparel production."),
            ("BFG-MUG-2", "Dual Station Mug Press", "Heat Presses", "Shenzhen PrintTech", "245.00", "165.00", 4, 2, Product.TRACK_SERIAL, "Two-station press for standard 11 oz sublimation mugs."),
            ("BFG-INK-100BK", "Sublimation Ink 100 ml - Black", "Consumables", "Sign Africa Distribution", "12.50", "6.80", 40, 10, Product.TRACK_QUANTITY, "High-density black sublimation ink for piezo print heads."),
            ("BFG-INK-CMY", "Sublimation Ink CMY Set", "Consumables", "Sign Africa Distribution", "32.00", "18.50", 24, 8, Product.TRACK_QUANTITY, "Cyan, magenta and yellow 100 ml sublimation ink set."),
            ("BFG-VIN-WHT", "Premium White Heat Transfer Vinyl 1 m", "Consumables", "Harare Office Supplies", "9.50", "4.20", 60, 15, Product.TRACK_QUANTITY, "Durable polyurethane HTV sold by the metre."),
            ("BFG-MUG-11", "11 oz Sublimation Mug - Box of 36", "Consumables", "Harare Office Supplies", "72.00", "46.00", 18, 5, Product.TRACK_QUANTITY, "Grade-A coated ceramic mugs packed in individual boxes."),
            ("BFG-LAP-I5", "Core i5 Business Laptop", "Computers & Accessories", "Harare Office Supplies", "690.00", "545.00", 6, 2, Product.TRACK_SERIAL, "15-inch business laptop with 16 GB RAM and 512 GB SSD."),
            ("BFG-UPS-1200", "1200 VA Line Interactive UPS", "Power Solutions", "SolarTech Zimbabwe", "135.00", "92.00", 10, 3, Product.TRACK_SERIAL, "Voltage regulation and backup power for office equipment."),
        ]
        products = {}
        for sku, name, category, supplier, price, cost, qty, reorder, tracking, description in product_rows:
            product, created = Product.objects.get_or_create(
                sku=sku,
                defaults={
                    "name": name,
                    "category": categories[category],
                    "supplier": suppliers[supplier],
                    "price": D(price),
                    "tax_rate": D("15.00"),
                    "quantity": 0,
                    "reorder_level": reorder,
                    "tracking_mode": tracking,
                    "description": description,
                    "is_active": True,
                },
            )
            self.created += int(created)
            products[sku] = product
            if created:
                StockMovement.objects.create(
                    product=product,
                    movement_type=StockMovement.IN,
                    quantity=qty,
                    unit_cost=D(cost),
                    note=f"[DEMO] Opening stock for {sku}",
                    user=self.actor,
                )
                self.created += 1

        combo = self.made(
            Combo.objects.get_or_create(
                code="starter-sublimation-studio",
                defaults={
                    "name": "Starter Sublimation Studio",
                    "description": "Printer, heat press and starter ink bundle.",
                    "discount_type": Combo.DISCOUNT_PERCENT,
                    "discount_value": D("7.50"),
                },
            )
        )
        for sku, quantity in [("BFG-EP-L3250", 1), ("BFG-HP-38", 1), ("BFG-INK-CMY", 1)]:
            self.made(
                ComboItem.objects.get_or_create(
                    combo=combo, product=products[sku], defaults={"quantity": quantity}
                )
            )
        return categories, suppliers, products

    def seed_customers(self):
        rows = [
            ("Makanaka Prints", "accounts@makanakaprints.example", "+263 772 410 520", "12 Harare Drive, Marlborough, Harare"),
            ("Blue Crane School", "bursar@bluecrane.example", "+263 242 611 840", "Borrowdale Road, Harare"),
            ("Tariro Events", "tariro@events.example", "+263 784 205 116", "Main Street, Gweru"),
            ("Matobo Creative Studio", "hello@matobocreative.example", "+263 713 660 410", "Fife Street, Bulawayo"),
            ("Nyasha Moyo", "nyasha.moyo@example.com", "+263 777 901 332", "Chitungwiza, Zimbabwe"),
            ("Zambezi Corporate Gifts", "procurement@zambezigifts.example", "+263 8677 300 118", "Msasa, Harare"),
        ]
        customers = {}
        for name, email, phone, address in rows:
            customers[name] = self.made(
                Customer.objects.get_or_create(
                    name=name,
                    defaults={"email": email, "phone": phone, "address": address},
                )
            )
        return customers

    def add_document_lines(self, document, rows):
        relation = "quotation" if isinstance(document, Quotation) else "invoice"
        for product, quantity, price in rows:
            lookup = {
                relation: document,
                "product": product,
                "description": product.name,
            }
            self.made(
                DocumentLine.objects.get_or_create(
                    **lookup,
                    defaults={
                        "quantity": D(str(quantity)),
                        "unit_price": D(str(price)),
                        "tax_rate_percent": product.tax_rate,
                        "line_total": D(str(quantity)) * D(str(price)),
                    },
                )
            )

    def seed_sales(self, customers, products, categories):
        quote_rows = [
            ("DEMO-Q-001", "Tariro Events", Quotation.SENT, -5, [("BFG-MUG-2", 1, "245.00"), ("BFG-MUG-11", 2, "70.00")]),
            ("DEMO-Q-002", "Blue Crane School", Quotation.DRAFT, -1, [("BFG-LAP-I5", 4, "665.00"), ("BFG-UPS-1200", 4, "125.00")]),
            ("DEMO-Q-003", "Makanaka Prints", Quotation.CONVERTED, -18, [("BFG-CAMEO5", 1, "450.00"), ("BFG-VIN-WHT", 10, "8.75")]),
        ]
        quotations = {}
        for number, customer, status, days, lines in quote_rows:
            quote = self.made(
                Quotation.objects.get_or_create(
                    number=number,
                    defaults={
                        "customer": customers[customer],
                        "date": self.today + timedelta(days=days),
                        "status": status,
                        "notes": "[DEMO] Prices valid for 14 days.",
                    },
                )
            )
            quotations[number] = quote
            self.add_document_lines(
                quote, [(products[sku], qty, price) for sku, qty, price in lines]
            )

        invoice_rows = [
            ("DEMO-INV-001", "Makanaka Prints", Invoice.PAID, -28, -14, "DEMO-Q-003", [("BFG-CAMEO5", 1, "450.00"), ("BFG-VIN-WHT", 10, "8.75")]),
            ("DEMO-INV-002", "Zambezi Corporate Gifts", Invoice.OVERDUE, -45, -15, None, [("BFG-MUG-11", 6, "68.00"), ("BFG-INK-CMY", 2, "30.00")]),
            ("DEMO-INV-003", "Matobo Creative Studio", Invoice.PENDING, -6, 24, None, [("BFG-EP-L3250", 1, "285.00"), ("BFG-INK-CMY", 1, "32.00")]),
            ("DEMO-INV-004", "Blue Crane School", Invoice.CONFIRMED, -2, 28, None, [("BFG-UPS-1200", 3, "128.00")]),
        ]
        invoices = {}
        for number, customer, status, date_days, due_days, quote_no, lines in invoice_rows:
            invoice = self.made(
                Invoice.objects.get_or_create(
                    number=number,
                    defaults={
                        "customer": customers[customer],
                        "date": self.today + timedelta(days=date_days),
                        "due_date": self.today + timedelta(days=due_days),
                        "status": status,
                        "quotation": quotations.get(quote_no),
                        "notes": "[DEMO] Thank you for your business.",
                        "created_by": self.actor,
                    },
                )
            )
            invoices[number] = invoice
            self.add_document_lines(
                invoice, [(products[sku], qty, price) for sku, qty, price in lines]
            )

        paid_invoice = invoices["DEMO-INV-001"]
        self.made(
            SalesPayment.objects.get_or_create(
                invoice=paid_invoice,
                note="[DEMO] Full EcoCash payment",
                defaults={
                    "amount": D("537.50"),
                    "date": self.today - timedelta(days=20),
                    "method": "EcoCash",
                },
            )
        )
        overdue = invoices["DEMO-INV-002"]
        self.made(
            SalesPayment.objects.get_or_create(
                invoice=overdue,
                note="[DEMO] Part payment by bank transfer",
                defaults={
                    "amount": D("150.00"),
                    "date": self.today - timedelta(days=30),
                    "method": "Bank Transfer",
                },
            )
        )
        self.made(
            StockReservation.objects.get_or_create(
                invoice=invoices["DEMO-INV-004"],
                product=products["BFG-UPS-1200"],
                defaults={"quantity": 3},
            )
        )
        self.made(
            PriceRule.objects.get_or_create(
                name="Demo Consumables Bulk Discount",
                defaults={
                    "rule_type": PriceRule.DISCOUNT,
                    "scope": PriceRule.CATEGORY,
                    "value_type": PriceRule.PERCENT,
                    "value": D("5.00"),
                    "category": categories["Consumables"],
                    "min_qty": 10,
                    "is_active": True,
                },
            )
        )
        return quotations, invoices

    def seed_shipments(self, suppliers, products):
        shipment = self.made(
            Shipment.objects.get_or_create(
                shipment_code="DEMO-SHP-001",
                defaults={
                    "name": "July Printing Equipment Consolidation",
                    "supplier": suppliers["Shenzhen PrintTech"],
                    "origin_country": "China",
                    "destination_country": "Zimbabwe",
                    "incoterm": Shipment.INCOTERM_CIF,
                    "shipping_method": Shipment.METHOD_SEA,
                    "eta_date": self.today + timedelta(days=21),
                    "status": Shipment.STATUS_IN_TRANSIT,
                    "allocation_basis": Shipment.COST_BASIS_VALUE,
                    "created_by": self.actor,
                },
            )
        )
        for sku, qty, price, hs_code in [
            ("BFG-HP-38", 8, "252.00", "8451.30"),
            ("BFG-MUG-2", 10, "148.00", "8516.79"),
        ]:
            self.made(
                ShipmentItem.objects.get_or_create(
                    shipment=shipment,
                    product=products[sku],
                    hs_code=hs_code,
                    defaults={
                        "quantity_expected": qty,
                        "unit_purchase_price": D(price),
                        "tracking_mode": products[sku].tracking_mode,
                    },
                )
            )
        for cost_type, description, amount in [
            (ShipmentCost.TYPE_FREIGHT, "Sea freight Durban to Harare", "680.00"),
            (ShipmentCost.TYPE_INSURANCE, "Marine cargo insurance", "85.00"),
        ]:
            self.made(
                ShipmentCost.objects.get_or_create(
                    shipment=shipment,
                    cost_type=cost_type,
                    description=description,
                    defaults={"amount": D(amount), "currency": "USD", "fx_rate": D("1.0")},
                )
            )

        received = self.made(
            Shipment.objects.get_or_create(
                shipment_code="DEMO-SHP-002",
                defaults={
                    "name": "Regional Printer Restock",
                    "supplier": suppliers["Sign Africa Distribution"],
                    "origin_country": "South Africa",
                    "destination_country": "Zimbabwe",
                    "incoterm": Shipment.INCOTERM_FOB,
                    "shipping_method": Shipment.METHOD_ROAD,
                    "eta_date": self.today - timedelta(days=15),
                    "arrival_date": self.today - timedelta(days=16),
                    "status": Shipment.STATUS_RECEIVED,
                    "created_by": self.actor,
                    "received_by": self.actor,
                    "received_at": timezone.now() - timedelta(days=15),
                },
            )
        )
        item = self.made(
            ShipmentItem.objects.get_or_create(
                shipment=received,
                product=products["BFG-EP-L3250"],
                hs_code="8443.32",
                defaults={
                    "quantity_expected": 3,
                    "quantity_received": 3,
                    "unit_purchase_price": D("198.00"),
                    "tracking_mode": Product.TRACK_SERIAL,
                    "landed_unit_cost": D("212.00"),
                    "landed_total_cost": D("636.00"),
                    "last_received_at": timezone.now() - timedelta(days=15),
                },
            )
        )
        for serial in ["DEMO-L3250-240701", "DEMO-L3250-240702", "DEMO-L3250-240703"]:
            self.made(
                ProductUnit.objects.get_or_create(
                    serial_number=serial,
                    defaults={
                        "product": products["BFG-EP-L3250"],
                        "shipment": received,
                        "shipment_item": item,
                        "purchase_price": D("198.00"),
                        "landed_cost": D("212.00"),
                        "status": ProductUnit.STATUS_AVAILABLE,
                        "created_by": self.actor,
                    },
                )
            )

    def seed_shop(self, products):
        rows = [
            ("DEMO-WEB-001", "Nyasha Moyo", "nyasha.moyo@example.com", Order.Status.PAID, [("BFG-INK-CMY", 1), ("BFG-VIN-WHT", 3)]),
            ("DEMO-WEB-002", "Tawanda Sibanda", "tawanda@example.com", Order.Status.PENDING, [("BFG-MUG-11", 1)]),
            ("DEMO-WEB-003", "Rudo Designs", "orders@rudodesigns.example", Order.Status.SHIPPED, [("BFG-HP-38", 1)]),
        ]
        for index, (number, name, email, status, lines) in enumerate(rows):
            total = sum(products[sku].price * qty for sku, qty in lines)
            order = self.made(
                Order.objects.get_or_create(
                    number=number,
                    defaults={
                        "email": email,
                        "full_name": name,
                        "total": total,
                        "status": status,
                        "created_at": timezone.now() - timedelta(days=index * 3 + 1),
                        "notes": "[DEMO] Online shop order",
                    },
                )
            )
            for sku, qty in lines:
                product = products[sku]
                self.made(
                    OrderItem.objects.get_or_create(
                        order=order,
                        product=product,
                        defaults={
                            "product_name": product.name,
                            "unit_price": product.price,
                            "quantity": qty,
                            "line_total": product.price * qty,
                        },
                    )
                )
            payment_status = (
                ShopPayment.Status.PAID
                if status in [Order.Status.PAID, Order.Status.SHIPPED]
                else ShopPayment.Status.INITIATED
            )
            self.made(
                ShopPayment.objects.get_or_create(
                    order=order,
                    defaults={
                        "provider": "paynow",
                        "provider_ref": f"DEMO-PAY-{index + 1:03d}",
                        "status": payment_status,
                        "raw_response": {"demo": True, "message": "Synthetic test payment"},
                    },
                )
            )

    def seed_finance_activity(self, company, currency, vat, bank, accounts, suppliers, customers, products):
        category_rows = [
            ("Electricity & Power", "5300"),
            ("Advertising & Promotions", "5400"),
            ("Courier & Local Deliveries", "5600"),
        ]
        expense_categories = {}
        for name, code in category_rows:
            expense_categories[name] = self.made(
                ExpenseCategory.objects.get_or_create(
                    company=company,
                    name=name,
                    defaults={"default_account": accounts[code], "default_tax": vat},
                )
            )
        for doc_no, days, payee, category, amount, status in [
            ("DEMO-EXP-001", -20, "ZETDC", "Electricity & Power", "186.40", Expense.POSTED),
            ("DEMO-EXP-002", -12, "Meta Ads", "Advertising & Promotions", "95.00", Expense.POSTED),
            ("DEMO-EXP-003", -3, "Swift Zimbabwe", "Courier & Local Deliveries", "42.50", Expense.DRAFT),
        ]:
            self.made(
                Expense.objects.get_or_create(
                    doc_no=doc_no,
                    defaults={
                        "company": company,
                        "date": self.today + timedelta(days=days),
                        "payee": payee,
                        "category": expense_categories[category],
                        "amount": D(amount),
                        "tax": vat,
                        "currency": currency,
                        "notes": "[DEMO] Synthetic operating expense",
                        "posted": status == Expense.POSTED,
                        "status": status,
                    },
                )
            )

        bill_rows = [
            ("DEMO-BILL-001", "Sign Africa Distribution", -35, -5, "920.00", "138.00", SupplierBill.PARTIAL, "Printer and consumables restock", "1300"),
            ("DEMO-BILL-002", "Harare Office Supplies", -8, 22, "410.00", "61.50", SupplierBill.POSTED, "Mugs and vinyl stock", "1300"),
        ]
        bills = {}
        for doc_no, supplier_name, date_days, due_days, subtotal, tax_amount, status, description, account_code in bill_rows:
            bill = self.made(
                SupplierBill.objects.get_or_create(
                    doc_no=doc_no,
                    defaults={
                        "company": company,
                        "supplier": suppliers[supplier_name],
                        "date": self.today + timedelta(days=date_days),
                        "due_date": self.today + timedelta(days=due_days),
                        "currency": currency,
                        "subtotal": D(subtotal),
                        "tax": D(tax_amount),
                        "total": D(subtotal) + D(tax_amount),
                        "status": status,
                    },
                )
            )
            bills[doc_no] = bill
            self.made(
                SupplierBillLine.objects.get_or_create(
                    bill=bill,
                    description=description,
                    defaults={
                        "qty": D("1"),
                        "unit_price": D(subtotal),
                        "account": accounts[account_code],
                        "tax": vat,
                    },
                )
            )

        self.made(
            ARPayment.objects.get_or_create(
                receipt_no="DEMO-RCPT-001",
                defaults={
                    "company": company,
                    "customer": customers["Makanaka Prints"],
                    "date": self.today - timedelta(days=20),
                    "currency": currency,
                    "bank": bank,
                    "amount": D("537.50"),
                    "method": "EcoCash",
                    "notes": "[DEMO] Settlement of DEMO-INV-001",
                },
            )
        )
        self.made(
            APPayment.objects.get_or_create(
                payment_no="DEMO-PAY-001",
                defaults={
                    "company": company,
                    "supplier": suppliers["Sign Africa Distribution"],
                    "date": self.today - timedelta(days=10),
                    "currency": currency,
                    "bank": bank,
                    "amount": D("500.00"),
                    "method": "Bank Transfer",
                    "notes": "[DEMO] Part payment on DEMO-BILL-001",
                },
            )
        )
        return bills

    def seed_credit_control(self, company, customers, suppliers, invoices, bills):
        debtor = self.made(
            DebtorAccount.objects.get_or_create(
                customer=customers["Zambezi Corporate Gifts"],
                defaults={
                    "company": company,
                    "total_invoiced": D("468.00"),
                    "total_paid": D("150.00"),
                    "total_outstanding": D("318.00"),
                    "current_balance": D("318.00"),
                    "overdue_balance": D("318.00"),
                    "credit_limit": D("1500.00"),
                    "risk_level": DebtorAccount.RiskLevel.MEDIUM,
                    "collection_status": DebtorAccount.CollectionStatus.PROMISE_TO_PAY,
                    "last_follow_up_date": self.today - timedelta(days=2),
                    "next_follow_up_date": self.today + timedelta(days=3),
                    "notes": "[DEMO] Regular corporate customer; payment awaiting approval.",
                },
            )
        )
        creditor = self.made(
            CreditorAccount.objects.get_or_create(
                supplier=suppliers["Sign Africa Distribution"],
                defaults={
                    "company": company,
                    "total_billed": D("1058.00"),
                    "total_paid": D("500.00"),
                    "total_outstanding": D("558.00"),
                    "current_balance": D("558.00"),
                    "overdue_balance": D("558.00"),
                    "risk_level": CreditorAccount.RiskLevel.MEDIUM,
                    "payment_status": CreditorAccount.PaymentStatus.NEGOTIATED,
                    "is_critical_supplier": True,
                    "notes": "[DEMO] Balance scheduled for the next payment run.",
                },
            )
        )
        follow_up = self.made(
            DebtorFollowUp.objects.get_or_create(
                customer=customers["Zambezi Corporate Gifts"],
                invoice=invoices["DEMO-INV-002"],
                subject="[DEMO] Follow-up on overdue invoice",
                defaults={
                    "company": company,
                    "debtor_account": debtor,
                    "follow_up_date": timezone.now() - timedelta(days=2),
                    "follow_up_type": DebtorFollowUp.FollowUpType.WHATSAPP,
                    "direction": DebtorFollowUp.Direction.OUTBOUND,
                    "message_summary": "Accounts team confirmed the invoice is queued for payment.",
                    "outcome": DebtorFollowUp.Outcome.PROMISED_TO_PAY,
                    "next_action_date": self.today + timedelta(days=3),
                    "promised_amount": D("318.00"),
                    "promised_payment_date": self.today + timedelta(days=2),
                    "created_by": self.actor,
                    "status": DebtorFollowUp.Status.COMPLETED,
                },
            )
        )
        self.made(
            PromiseToPay.objects.get_or_create(
                customer=customers["Zambezi Corporate Gifts"],
                invoice=invoices["DEMO-INV-002"],
                promised_date=self.today + timedelta(days=2),
                defaults={
                    "company": company,
                    "follow_up": follow_up,
                    "promised_amount": D("318.00"),
                    "status": PromiseToPay.Status.OPEN,
                    "notes": "[DEMO] Customer expects internal approval within two days.",
                    "created_by": self.actor,
                },
            )
        )
        self.made(
            CollectionTask.objects.get_or_create(
                customer=customers["Zambezi Corporate Gifts"],
                invoice=invoices["DEMO-INV-002"],
                task_type=CollectionTask.TaskType.CHECK_PROMISE,
                defaults={
                    "company": company,
                    "assigned_to": self.actor,
                    "due_date": self.today + timedelta(days=3),
                    "priority": CollectionTask.Priority.HIGH,
                    "status": CollectionTask.Status.PENDING,
                    "notes": "[DEMO] Confirm that the promised balance was received.",
                    "created_by": self.actor,
                },
            )
        )
        self.made(
            CollectionTask.objects.get_or_create(
                supplier=suppliers["Sign Africa Distribution"],
                bill=bills["DEMO-BILL-001"],
                task_type=CollectionTask.TaskType.FOLLOW_UP_CREDITOR,
                defaults={
                    "company": company,
                    "assigned_to": self.actor,
                    "due_date": self.today + timedelta(days=1),
                    "priority": CollectionTask.Priority.MEDIUM,
                    "status": CollectionTask.Status.IN_PROGRESS,
                    "notes": "[DEMO] Send remittance advice and confirm balance date.",
                    "created_by": self.actor,
                },
            )
        )
        self.made(
            FollowUpTemplate.objects.get_or_create(
                company=company,
                name="Friendly payment reminder",
                audience=FollowUpTemplate.Audience.DEBTOR,
                channel=FollowUpTemplate.Channel.WHATSAPP,
                tone=FollowUpTemplate.Tone.FRIENDLY,
                defaults={
                    "subject": "Payment reminder for {invoice_number}",
                    "body": "Hello {customer_name}, this is a friendly reminder that {amount_due} is due on {due_date}.",
                },
            )
        )
        self.made(
            ReminderRule.objects.get_or_create(
                company=company,
                audience=ReminderRule.Audience.DEBTOR,
                offset_days=3,
                name="Three days overdue",
                defaults={
                    "task_type": CollectionTask.TaskType.SEND_REMINDER,
                    "priority": CollectionTask.Priority.HIGH,
                },
            )
        )
