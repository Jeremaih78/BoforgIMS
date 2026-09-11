"""Conservative validation for business supporting documents."""
from pathlib import Path
from django.core.exceptions import ValidationError


def validate_document(upload):
    if upload.size > 10 * 1024 * 1024:
        raise ValidationError('Document must be 10MB or smaller.')
    extension = Path(upload.name).suffix.lower()
    if extension not in {'.pdf', '.png', '.jpg', '.jpeg', '.docx', '.xlsx'}:
        raise ValidationError('Use a PDF, PNG, JPEG, DOCX or XLSX document.')
    header = upload.read(8)
    upload.seek(0)
    signatures = {'.pdf': b'%PDF-', '.png': b'\x89PNG\r\n\x1a\n',
                  '.jpg': b'\xff\xd8\xff', '.jpeg': b'\xff\xd8\xff',
                  '.docx': b'PK\x03\x04', '.xlsx': b'PK\x03\x04'}
    if not header.startswith(signatures[extension]):
        raise ValidationError('The document contents do not match its file extension.')
