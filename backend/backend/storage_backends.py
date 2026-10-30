"""
Optional Vercel Blob storage backend.

Vercel Functions have a read-only filesystem, so uploaded documents, evidence
and generated certificate PDFs cannot be written to MEDIA_ROOT there. When the
BLOB_READ_WRITE_TOKEN environment variable is present, settings.py switches the
default storage to this class. Local development keeps using the normal
FileSystemStorage, so nothing changes outside Vercel.

Files are stored as *private* blobs by default (BLOB_ACCESS=private) because
they contain CNIC documents and face images. They are only served through the
existing authenticated Django views, never directly from a public URL.
"""

import mimetypes
import os

from django.conf import settings
from django.core.files.base import ContentFile
from django.core.files.storage import Storage
from django.utils.deconstruct import deconstructible


def _blob():
    # Imported lazily so the dependency is only needed when Blob is enabled.
    from vercel.blob import sync as blob_sync
    return blob_sync


@deconstructible
class VercelBlobStorage(Storage):
    def __init__(self, access=None):
        self.access = access or os.environ.get('BLOB_ACCESS', 'private')

    def _save(self, name, content):
        if hasattr(content, 'seek'):
            try:
                content.seek(0)
            except Exception:
                pass
        data = content.read()
        if isinstance(data, str):
            data = data.encode('utf-8')
        content_type = (
            getattr(content, 'content_type', None)
            or mimetypes.guess_type(name)[0]
            or 'application/octet-stream'
        )
        result = _blob().put_blob(
            name.replace('\\', '/'),
            bytes(data),
            access=self.access,
            content_type=content_type,
            add_random_suffix=True,
        )
        return result.pathname

    def _open(self, name, mode='rb'):
        result = _blob().get_blob(name, access=self.access)
        return ContentFile(result.body, name=name)

    def exists(self, name):
        # Every upload gets a random suffix, so names never collide.
        return False

    def delete(self, name):
        if name:
            _blob().delete_blob(name)

    def size(self, name):
        return len(self._open(name).read())

    def url(self, name):
        return f"{settings.MEDIA_URL}{name}"

    def path(self, name):
        raise NotImplementedError('Vercel Blob files have no local filesystem path.')
