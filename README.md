# Boforg IMS (Django)

A clean full‑stack Inventory & Sales management system for **Boforg Technologies**.

## Quickstart

```bash
python -m venv .venv && source .venv/bin/activate  # Windows: .venv\Scripts\activate
pip install -r requirements.txt
python manage.py migrate
python manage.py createsuperuser
python manage.py runserver
```

Login at http://127.0.0.1:8000 and use the top navigation.

### PDF Rendering
We use **WeasyPrint**. On Linux you may need extra packages (Debian/Ubuntu):

```bash
sudo apt-get install libpango-1.0-0 libcairo2 libffi-dev shared-mime-info
```

### Switch to Postgres (production)

Set env vars before running:

```
export DB_ENGINE=postgres
export POSTGRES_DB=boforg_ims
export POSTGRES_USER=postgres
export POSTGRES_PASSWORD=yourpass
export POSTGRES_HOST=localhost
export POSTGRES_PORT=5432
export DEBUG=0
export DJANGO_SECRET_KEY='your-secret'
```

## Apps

- `inventory` – products, categories, suppliers, stock movements
- `customers` – customer records
- `sales` – quotations, invoices, payments + PDF download
- `users` – dashboard and auth routes (uses Django auth & Groups)

## Roles

Create two groups in the admin:
- **Admin** – full access
- **Staff** – can add/edit products, create quotations & invoices

Assign users to groups via `/admin/`.
## Legal pages & Meta data deletion

Public URLs:
- Privacy: https://boforg.co.zw/legal/privacy/
- Terms: https://boforg.co.zw/legal/terms/
- Data Deletion (instructions): https://boforg.co.zw/legal/data-deletion/
- Deletion Callback (POST): https://boforg.co.zw/legal/facebook-data-deletion/

Environment:
- META_APP_SECRET="<SET_THIS_VALUE_FROM_META_APP_DASHBOARD>"
- PUBLIC_BASE_URL="https://boforg.co.zw"

To refresh secrets on Linux (systemd):
- Add `Environment="META_APP_SECRET=..."` to the unit or drop-in, or `export META_APP_SECRET=...` in the shell before running `python manage.py` for ad-hoc use.

Restart services after updating secrets:
- sudo systemctl restart gunicorn-ims
- sudo systemctl reload nginx

Meta App Review fields:
- Privacy Policy URL: https://boforg.co.zw/legal/privacy/
- Terms of Service URL: https://boforg.co.zw/legal/terms/
- Data Deletion Instructions: https://boforg.co.zw/legal/data-deletion/
- Data Deletion Callback (optional): https://boforg.co.zw/legal/facebook-data-deletion/
