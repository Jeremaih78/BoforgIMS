# Website and shop upgrade

## Before implementation

Existing branch: `review/ims-reliability`; pending IMS and user changes preserved.
Baseline: 172 PostgreSQL tests passed in the immediately preceding review run.
Architecture: Django templates, django-hosts, shared inventory Product/Category/
Combo models, session carts, transactional shop orders and Paynow verification.
The main website uses home.css; shop pages independently load runtime Tailwind
and duplicate navigation. Legacy local routes use /shop/; public shop uses /.

| Severity | Finding | Planned response |
|---|---|---|
| High | Cart next parameter permits external redirects | Same-host redirect validation |
| High | Active internal products can appear publicly | Explicit visibility and shared public selector, recheck at checkout |
| High | Cart checks race; UI permits out-of-stock submissions | Cart/product locking and consistent availability controls |
| Medium | Shop is a flat catalogue with fragmented navigation | Shared public shell, guided departments, searchable catalogue |
| Medium | Search misses partial words/category/SKU | Bounded multi-field search and URL-preserving filters |
| Medium | Live stock is cached on website | Fresh bounded public-product queries |
| Medium | Hand-built product JSON can become invalid | Server-serialized escaped JSON-LD |

Primary files: inventory models/admin/migration; shop selectors/views/utils/
services/tests; website views; public templates/static assets; host routes.
Schema changes will be additive and reversible. Product visibility defaults
preserve existing active priced stock products; staff must classify internal
records before release. No production catalogue is inspected or rewritten.
Combos currently discount invoice components, while shop carts hold individual
products only. Do not silently drop discounts or invent a second stock system.

## Checklist

- [x] Architecture, baseline, Git preservation, risk assessment.
- [x] Shared navigation, footer, responsive public design.
- [x] Homepage decision paths and guided shop landing.
- [x] Visibility, departments, product discovery and recommendations.
- [x] Product detail, cart, WhatsApp and stock validation.
- [x] SEO and non-blocking analytics.
- [x] Regression, migration, responsive browser verification.
- [x] Deployment, rollback and limitations.


## Implemented results

### Architecture, UX and conversion

The existing Django application and single IMS inventory remain in place. A shared
public shell now provides consistent navigation, a touch menu with Escape support,
search/cart links, footer, focus states and one WhatsApp assistance control. Shop
pages no longer need runtime Tailwind or HTMX to function. Existing homepage
training, software, stories and FAQ content is retained; the FAQ script is restored.
The original blue identity is retained; WhatsApp assistance uses green.

The homepage leads with Find My Starter Package and Shop Machines & Supplies,
followed by four customer decision paths. The shop root is guided; `?all=1`
retains the full catalogue. Departments, categories, published packages, recent
products and available machine/consumable/blank sections guide discovery. The
latter sections use existing category labels and render only when data exists.
We do not label recent products as best sellers without sales evidence.

Shared product cards show price/currency, optional staff-authored benefit text,
availability, details, and a native POST add button only when purchasable. Product
pages expose overview, product-specific WhatsApp enquiries, purchasing controls,
collection/support guidance, curated recommendations and related products.
Existing single-image storage is reused; a multi-image gallery was not invented.

Package cards use existing IMS Combo prices, quantities and discounts. Staff opt
packages into publication; only valid, public, active, positive-price components
in one currency qualify. The cart retains a package as a unit. Checkout expands
its components, locks stock, aggregates overlapping demand and records the IMS
package discount separately on the order. Payment/retry behavior uses the existing
order service, not another inventory database. Ordinary products and packages may
share components without overselling. Current prices are recalculated at checkout.

WhatsApp messages are encoded centrally with product details or product/package
cart lines. Opening WhatsApp creates an enquiry, not a reservation/payment. No fake
order reference is generated for an unsubmitted enquiry. Confirmed checkout orders
keep their existing order numbers. No customer messages or provider calls were sent.

### Important files and database changes

- `inventory/models.py`, `admin.py`: category departments, product visibility,
  short descriptions, curated product relationships and opt-in package publication.
- `shop/selectors.py`, `packages.py`: shared public-product/package boundary.
- `shop/models.py`, `services.py`, `utils.py`, `views.py`: package carts,
  transactional component checkout, validation, safe redirects, search and filters.
- `inventory/api.py`: anonymous API reads respect the same visibility boundary;
  authorized IMS staff keep access to internal records.
- `shop/context_processors.py`: host-aware public links, shared configured WhatsApp
  number, cart count and optional analytics event handoff.
- `templates/public/`, `templates/shop/`, homepage partials and
  `static/css/storefront.css`, `static/js/storefront.js`: reusable responsive UI.
- `shop/seo.py`, shop/website routes: product/category sitemaps and robots endpoints.
- `shop/test_storefront.py`: 19 additional regression tests.

Three additive migrations:

1. `inventory/0014_category_department_combo_is_public_and_more` adds merchandising
   fields. Categories start unclassified; packages start unpublished. Product
   visibility defaults true to preserve active catalogue records. Zero/negative
   prices and inactive records are excluded from public shopping, not deleted.
2. `shop/0002_cartpackage` adds session package lines with a uniqueness constraint.
3. `inventory/0015_category_category_name_search_gin_and_more` enables `pg_trgm`
   if needed and adds four uppercase trigram GIN indexes matching Django's
   case-insensitive searches. Its reversal removes our indexes but deliberately
   keeps the shared extension. The database role must be able to enable this
   trusted extension, or the DBA must enable it first. Standard index builds can
   block writes: schedule maintenance for a large catalogue.

No new Python or frontend dependency was added in this pass. The earlier IMS
review's dependency upgrades and eleven migrations still apply if not deployed.
`BOFORG_WHATSAPP_NUMBER` defaults to the existing configured business number and
can be overridden per machine. `SHOP_PUBLIC_BASE`, if set, must be the externally
reachable shop origin; otherwise payment callbacks use the current request origin.

### Security, SEO, analytics and performance

Public reads, add-to-cart and checkout reject hidden/inactive/invalid-price
products. Quantity and identifier validation rejects malformed input. Cart adds
and removals coordinate with checkout through cart row locks; stock is locked and
rechecked at order creation. External `next` redirects are rejected. Customer
content remains escaped; Product/Breadcrumb/Organization JSON-LD is server-encoded
with script terminators escaped. Product/category sitemap queries exclude private
records. Canonicals, titles, descriptions and social metadata accompany public pages.

Existing GA4 ID and internal-user exclusion are retained. One optional JS helper
handles view/list/cart/checkout, successful native cart changes, session-deduplicated
purchase events and WhatsApp/category/package clicks. Events contain no customer
email or order notes. Click events are delegated once; refreshing after an add does
not resend the add event. Browser local checks block external analytics requests.
GA4 ingestion/attribution and consent policy are not certified by these local tests;
session-based purchase deduplication is not a universal cross-device guarantee.

Measured on the same isolated synthetic data with a fresh Django test client:

| Page | Before local optimization | After |
|---|---:|---:|
| Homepage | 9 queries | 3 queries |
| Product detail | 18 queries | 3 queries |
| Full catalogue | 3 queries | 3 queries |

These measurements were taken during this pass, not against production. Removing
unused homepage queries and cart/session creation on passive product browsing
caused the improvement. Product recommendations are bounded, catalogue pages hold
12 products, published home packages are bounded to 12, and sitemap pages to 1000.
No stale stock cache was introduced. Short search strings and cross-table OR
searches can still require scans; validate actual production query plans and
latency before making scale claims. Trigram indexes support selective searches.

## Verification and evidence

- Full suite: **191 PostgreSQL tests passed in 17.570 seconds** (172 existing + 19
  new). Failure-path tests intentionally log mocked posting/AI exceptions.
- New tests cover visibility, public API, department/category/SKU search, price
  filters, out-of-stock behavior, malformed IDs/quantities, unsafe redirects,
  script escaping, recommendations, package discounts, combined component demand,
  reservation/finalization/retry, cart WhatsApp/removal, and analytics refresh.
- All three CRO migrations applied, reversed and reapplied on separate isolated
  PostgreSQL database `ims_review_migrations`; migration drift check clean.
- Django system checks, production-setting checks, static collection and
  `git diff --check` passed. Production checks used a temporary test secret and
  did not contact the live database, provider or VPS.
- Chrome desktop engine at widths **320, 360, 375, 390, 412, 430, 768, 1024, 1440**:
  **63 page/width checks**, all HTTP 200, no page-level horizontal overflow,
  no JavaScript errors. Seven pages: home, shop, fitness filter, search, product,
  populated cart and checkout. Menu/Escape, native product add, department filter
  and encoded WhatsApp link were exercised. Additional phone checks passed for FAQ expansion, package add/remove and absence of the unavailable product purchase control. This is viewport testing, not a
  physical Android-device or exhaustive WCAG certification.
- [Layout results](shop-evidence/layout.json), [workflow checks](shop-evidence/workflows.json),
  [phone shop](shop-evidence/shop-390.png), [phone product](shop-evidence/product-390.png),
  [phone cart](shop-evidence/cart-390.png), [desktop homepage](shop-evidence/home-1440.png).

## Owner review and genuine remaining work

1. Resolve the existing exposed credential reported in `IMS_REVIEW.md` before
   committing/sharing. Its value was not used or repeated in this pass.
2. In Django administration, classify existing categories as Printing or Fitness,
   hide internal/transport products, and review/publish appropriate combos. Until
   then unclassified categories appear only in All Products, and packages remain
   unavailable for public selection. Existing records were not mass-classified.
3. Enter accurate short descriptions and compatible recommended products; replace
   missing images and review retained historical service/testimonial/profit copy.
   No compatibility, training inclusion, earnings, popularity or new business
   promises were fabricated. No automatic stock-notification subscription service
   was added; unavailable products offer WhatsApp assistance.
4. Check cross-subdomain CSRF/session cookie configuration in staging, exercise
   Paynow sandbox callbacks/amounts and validate GA4 DebugView with approved test
   identifiers. Production host paths depend on the deployed router, not the
   legacy localhost `/shop/` prefix. Paynow initiation remains the earlier GET
   workflow; payment expiry/manual reconciliation remain separate work.
5. Earlier IMS release blockers (VAT policy, historical reconciliation, private
   media migration and Ubuntu native PDF validation) remain in `IMS_REVIEW.md`.

## Deployment and rollback (not performed)

The repository production guide names **gunicorn-ims** and discovers an existing
`venv` or `.venv`. Its older Nginx path examples predate current host routing;
do not copy those snippets over a functioning server. First verify the real
service WorkingDirectory and Python executable, make and test database/media
backups, and validate this release in staging. Keep `.env` untracked and preserve
the production secret and credentials. Do not deploy `config.test_settings`.

From the confirmed server checkout, with the actual service environment and its
virtual environment activated, in an approved maintenance window:

```bash
git status --short
# Stop if there are unreviewed local changes; review the target release first.
git pull --ff-only
python -m pip install -r requirements.txt
python -m pip check
python manage.py check_ims_integrity > integrity-before-cro.json
python manage.py migrate --plan
python manage.py migrate --noinput
python manage.py check --deploy
python manage.py collectstatic --noinput
sudo systemctl restart gunicorn-ims
sudo systemctl is-active --quiet gunicorn-ims
```

These commands were not run on the VPS. The Python interpreter must be the one
used by that service; use the discovery procedure in the existing production
guide if it is unclear. Run tests in staging with a separate test database, not
against the production service environment. Enable `pg_trgm` through the DBA
before migration if the application role cannot do so. Complete catalogue
classification and public-host smoke checks before reopening traffic.

Before new traffic, the CRO-specific migrations can be reversed with the new
code: `python manage.py migrate shop 0001`, then
`python manage.py migrate inventory 0013`. This drops package carts and the new
merchandising fields/indexes; export those edits first. It keeps pg_trgm. Then
restore the previous reviewed code/static release and restart gunicorn-ims.
Do not use this to undo the earlier IMS review; its separate rollback is in
`IMS_REVIEW.md`. After accepting new orders, prefer a forward fix: preserve new
order component/discount records and reconcile any post-backup transactions
before considering restoration. No production rollback was performed.

Temporary loopback Django and isolated PostgreSQL services were stopped after verification. No business database was migrated and no deployment was performed.


## Follow-up: add without leaving the catalogue (2026-09-11)

Same-origin product/package add forms now use progressive fetch submission. A
four-second accessible notification names the added item, the header count updates,
and the URL/scroll position stay unchanged. Rapid additions are queued and duplicate
submissions of a pending form are prevented. Native forms remain available without
JavaScript. Network uncertainty tells the visitor to check the cart before retrying;
stock errors do not show a success notice. Async analytics is sent once from the
response rather than being queued for the next page load.

Verified 25 shop tests (including three new response/error regressions), Django
checks and whitespace checks. Chrome at 390px verified unchanged page identity,
URL and scroll position, notification text, two repeated additions, persisted cart
quantity, and an error notification with an unchanged count. Stock rejection is
covered on the backend; the browser error response was simulated. Evidence:
[add notification](shop-evidence/add-to-cart-toast-390.png). No new migrations.


### Follow-up: consistent removal and notifications

Product and package removals now return refreshed cart contents, totals and count
without navigation. The cart retains its occupied height for the current page so
removing the last row does not force a scroll jump. All public-site notifications,
including Django flash messages and errors, use the same blue bubble and five-second
duration; errors are identified in text. Native form fallback remains available.
Verified 27 shop tests and Chrome at 390/1440px: product/package removal, subtotal,
empty state, unchanged page identity/scroll and matching add/remove/error colours.
Screenshots: `shop-evidence/remove-toast-390.png` and `remove-toast-1440.png`.
