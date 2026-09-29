# Dependency audit (2026-09-10)

Source: pip-audit / PyPI advisory database. Counts include aliased advisories and are not unique CVE counts.

| Package | Pinned version | Advisory entries | Fix versions reported |
|---|---|---|---|
| brotli | 1.1.0 | 2 | 1.2.0 |
| cryptography | 45.0.7 | 11 | 46.0.5, 46.0.6, 46.0.7, 48.0.1, 49.0.0, 50.0.0 |
| django | 5.2.6 | 61 | 4.2.25, 4.2.26, 4.2.27, 4.2.28, 4.2.29, 4.2.30, 5.1.13, 5.1.14, 5.1.15, 5.2.11, 5.2.12, 5.2.13, 5.2.14, 5.2.15, 5.2.16, 5.2.17, 5.2.7, 5.2.8, 5.2.9, 6.0.2, 6.0.3, 6.0.4, 6.0.5, 6.0.6, 6.0.7, 6.0.8 |
| fonttools | 4.59.2 | 1 | 4.60.2 |
| idna | 3.10 | 2 | 3.15 |
| lxml | 6.0.1 | 2 | 6.1.0 |
| pillow | 11.3.0 | 35 | 12.1.1, 12.2.0, 12.3.0 |
| pypdf | 6.0.0 | 40 | 6.1.3, 6.10.0, 6.10.1, 6.10.2, 6.12.0, 6.12.1, 6.12.2, 6.13.0, 6.13.1, 6.13.3, 6.14.0, 6.14.1, 6.14.2, 6.15.0, 6.16.0, 6.16.1, 6.4.0, 6.6.0, 6.6.2, 6.7.1, 6.7.2, 6.7.3, 6.7.4, 6.7.5, 6.8.0, 6.9.1, 6.9.2 |
| python-dotenv | 1.1.1 | 1 | 1.2.2 |
| requests | 2.32.5 | 1 | 2.33.0 |
| urllib3 | 2.5.0 | 8 | 2.6.0, 2.6.3, 2.7.0 |
| sqlparse | 0.5.3 | 10 | 0.5.4, 0.6.0 |
| weasyprint | 66.0 | 3 | 68.0, 70.0 |
| djangorestframework | 3.15.2 | 2 | 3.17.2 |

Django upstream security notice: https://www.djangoproject.com/weblog/2026/aug/04/security-releases/

The full machine-readable audit is in ignored `.local-review/dependency-audit.json`.


## Remediation verified

Security-related pins were updated, including required tinycss2 compatibility for
WeasyPrint 70.0. Installation in `.local-review/app` succeeded, `pip check` passed,
and a new pip-audit resolved 69 packages with zero known advisory matches.
The original application virtual environment was preserved. Ubuntu native renderer
and provider integration validation still require staging.

Vendor sources consulted:
- https://www.djangoproject.com/weblog/2026/aug/04/security-releases/
- https://pillow.readthedocs.io/en/stable/releasenotes/12.3.0.html
- https://cryptography.io/en/stable/
- https://doc.courtbouillon.org/weasyprint/latest/first_steps.html

Advisory counts above include aliases; they must not be presented as unique CVEs.
No advisory matches is a point-in-time dependency scan result, not a security certification.
