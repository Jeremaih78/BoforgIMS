# Boforg IMS (Django)

A unified platform for Boforg Technologies Private Limited covering the internal Inventory Management System (IMS), the public marketing site, and the customer-facing shop.

## Quickstart

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate
source .venv/bin/activate
pip install -r requirements.txt
python manage.py migrate
python manage.py createsuperuser
python manage.py runserver
```

To populate a development database with realistic, repeatable demo data:

```bash
python manage.py seed_demo_data
```

The command covers inventory, customers, sales, shop orders, finance, shipments,
serialized stock, and credit control. It is idempotent, so rerunning it does not
duplicate the demo records.

Visit http://127.0.0.1:8000 to access the IMS dashboard under `/ims/`.

### PDF Rendering

We rely on **WeasyPrint**. On Debian/Ubuntu install the system packages:

```bash
sudo apt-get install libpango-1.0-0 libcairo2 libffi-dev shared-mime-info
```

### PostgreSQL Configuration

```bash
export POSTGRES_DB=boforg_ims
export POSTGRES_USER=boforg
export POSTGRES_PASSWORD=yourpass
export POSTGRES_HOST=localhost
export POSTGRES_PORT=5432
export DJANGO_SECRET_KEY="your-secret"
export DJANGO_DEBUG=false
```

## Public Web Frontends

- **Company website**: `https://boforg.co.zw/`
- **Shop**: `https://shop.boforg.co.zw/`
- **IMS**: `https://ims.boforg.co.zw/`
- **AI Tasker**: `https://ai.boforg.co.zw/`
- **REST API**: `https://api.boforg.co.zw/`

`config/hosts.py` selects one URL configuration from `config/host_urls/` for
each hostname. All hosts still use the same Django process, applications,
authentication system, PostgreSQL database, static files, and media storage.
The path-based `config/urls.py` remains available for local development and
legacy reverse compatibility; old public `/shop/`, `/ims/`, and
`/ims/tasker/` entry points redirect to their canonical hosts.

The API host exposes the existing inventory and sales API views under
`/inventory/` and `/sales/`. Product catalogue reads are public; product
writes, shipment data, and invoice serial operations require authentication.

Production host settings:

```bash
export DJANGO_PARENT_HOST="boforg.co.zw"
export DJANGO_HOST_SCHEME="https"
export DJANGO_ALLOWED_HOSTS="boforg.co.zw,www.boforg.co.zw,shop.boforg.co.zw,ims.boforg.co.zw,ai.boforg.co.zw,api.boforg.co.zw"
export CSRF_TRUSTED_ORIGINS="https://boforg.co.zw,https://www.boforg.co.zw,https://shop.boforg.co.zw,https://ims.boforg.co.zw,https://ai.boforg.co.zw,https://api.boforg.co.zw"
export SESSION_COOKIE_DOMAIN=".boforg.co.zw"
export CSRF_COOKIE_DOMAIN=".boforg.co.zw"
```

## Apps

- `website` – corporate homepage and static marketing pages
- `shop` – public catalogue, cart, checkout, Paynow integration
- `inventory` – products, categories, suppliers, stock movements
- `customers` – customer records
- `sales` – quotations, invoices, payments + PDF download
- `users` – dashboard and auth routes (Django auth & groups)
- `accounting` – financial posting and ledger utilities
- `legal` – privacy/terms/data deletion pages
- `payments` – gateway wrappers (Paynow)

## Environment & Services

### Paynow Payments

```bash
export PAYNOW_INTEGRATION_ID="<your-id>"
export PAYNOW_INTEGRATION_KEY="<your-key>"
export PAYNOW_BASE="https://www.paynow.co.zw"  # sandbox URL if testing
export SHOP_PUBLIC_BASE="https://boforg.co.zw"
```

`SHOP_PUBLIC_BASE` is used to build the `return` and `result` callback URLs. Keep Redis configured (`REDIS_URL=redis://...`) to enable homepage/catalog caching.

### Sessions & Caching

`SESSION_ENGINE=django.contrib.sessions.backends.cached_db` by default. Point `REDIS_URL` to production Redis for cache + session storage; falls back to per-process locmem cache in development.

## Roles

Create two groups in `/admin/`:

- **Admin** – full access
- **Staff** – manage catalogue, quotations, and invoices

Assign users to these groups for IMS permissions.

## Legal pages & Meta data deletion

Public URLs:
- Privacy: https://boforg.co.zw/legal/privacy/
- Terms: https://boforg.co.zw/legal/terms/
- Data Deletion (instructions): https://boforg.co.zw/legal/data-deletion/
- Deletion Callback (POST): https://boforg.co.zw/legal/facebook-data-deletion/

Environment:
- `META_APP_SECRET="<SET_THIS_VALUE_FROM_META_APP_DASHBOARD>"`
- `PUBLIC_BASE_URL="https://boforg.co.zw"`

To refresh secrets on Linux (systemd):
- Add `Environment="META_APP_SECRET=..."` to the unit or drop-in, or `export META_APP_SECRET=...` before running `python manage.py` for ad-hoc use.

Restart services after updating secrets:
- `sudo systemctl restart gunicorn-ims`
- `sudo systemctl reload nginx`

Meta App Review fields:
- Privacy Policy URL: https://boforg.co.zw/legal/privacy/
- Terms of Service URL: https://boforg.co.zw/legal/terms/
- Data Deletion Instructions: https://boforg.co.zw/legal/data-deletion/
- Data Deletion Callback (optional): https://boforg.co.zw/legal/facebook-data-deletion/
