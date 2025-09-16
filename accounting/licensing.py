import hashlib
from .models import Company


def validate_license(company: Company) -> bool:
    """Very simple stub: license is SHA1 of company name reversed.

    Real impl should verify a signed token. This stub is for development/testing.
    """
    if not company.license_key:
        return True  # allow if unset in dev
    expected = hashlib.sha1(company.name[::-1].encode("utf-8")).hexdigest()[:16].upper()
    return company.license_key.strip().upper() == expected

