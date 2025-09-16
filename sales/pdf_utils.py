from io import BytesIO
import os
from urllib.parse import urlparse


def render_pdf_from_html(html: str, base_url: str | None = None) -> bytes:
    """
    Render PDF from HTML.

    Tries WeasyPrint first (best CSS support). If its native deps are missing
    on Windows (e.g., libgobject), falls back to xhtml2pdf for portability.
    """
    # First try WeasyPrint
    try:
        from weasyprint import HTML  # type: ignore

        return HTML(string=html, base_url=base_url).write_pdf()
    except Exception:
        # Fallback: xhtml2pdf (limited CSS support but no GTK deps)
        from xhtml2pdf import pisa  # type: ignore
        from django.conf import settings
        from django.contrib.staticfiles import finders

        def link_callback(uri: str, rel: str) -> str:
            """Convert HTML URIs to absolute system paths for xhtml2pdf.

            Handles /static/ and /media/ URLs, and absolute http(s) URLs pointing
            to those prefixes by mapping them to local filesystem files.
            """
            try:
                parsed = urlparse(uri)
                path = parsed.path if parsed.scheme else uri

                # Static files
                static_url = getattr(settings, 'STATIC_URL', '/static/') or '/static/'
                if path.startswith(static_url):
                    rel_path = path[len(static_url):]
                    absolute_path = finders.find(rel_path)
                    if not absolute_path:
                        static_root = getattr(settings, 'STATIC_ROOT', '')
                        if static_root:
                            candidate = os.path.join(static_root, rel_path)
                            if os.path.isfile(candidate):
                                absolute_path = candidate
                    return absolute_path or uri

                # Media files
                media_url = getattr(settings, 'MEDIA_URL', '/media/') or '/media/'
                if path.startswith(media_url):
                    rel_path = path[len(media_url):]
                    media_root = getattr(settings, 'MEDIA_ROOT', '')
                    candidate = os.path.join(media_root, rel_path)
                    if os.path.isfile(candidate):
                        return candidate
                    return uri
            except Exception:
                pass
            # Default: return original URI (xhtml2pdf may try to fetch it)
            return uri

        result = BytesIO()
        pisa.CreatePDF(html, dest=result, link_callback=link_callback)  # type: ignore[arg-type]
        return result.getvalue()
