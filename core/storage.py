"""Business evidence is stored outside the publicly served media directory."""
from django.conf import settings
from django.core.files.storage import FileSystemStorage, default_storage
from django.urls import reverse


class PrivateDocumentStorage(FileSystemStorage):
    def __init__(self):
        super().__init__(location=settings.PRIVATE_MEDIA_ROOT)

    def url(self, name):
        return reverse('ims:private_document', kwargs={'name': name})

    def _open(self, name, mode='rb'):
        try:
            return super()._open(name, mode)
        except FileNotFoundError:
            # Read compatibility only. Existing public copies require the
            # documented owner-run migration before confidentiality is assured.
            return default_storage.open(name, mode)

    def exists(self, name):
        return super().exists(name) or default_storage.exists(name)


def private_document_storage():
    return PrivateDocumentStorage()
