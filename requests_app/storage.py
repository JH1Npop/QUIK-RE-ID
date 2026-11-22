"""Private Cloudinary storage for request uploads.

Every file is uploaded with type="authenticated", so Cloudinary refuses unsigned
requests for it. The browser never gets a Cloudinary link: the upload view checks
who is asking, fetches the file here with a signed URL, and streams it back.
"""
import os

import cloudinary.uploader
import cloudinary.utils
import requests
from cloudinary_storage.storage import MediaCloudinaryStorage
from django.core.files.base import ContentFile
from django.core.files.uploadedfile import UploadedFile
from django.utils.deconstruct import deconstructible

DELIVERY_TYPE = "authenticated"
RAW_EXTENSIONS = {".pdf"}  # PDFs are stored as raw files, images as images


@deconstructible
class PrivateCloudinaryStorage(MediaCloudinaryStorage):
    def _get_resource_type(self, name):
        return "raw" if os.path.splitext(name)[1].lower() in RAW_EXTENSIONS else "image"

    def _public_id(self, name):
        # Raw files keep their extension in the public ID; images don't.
        if self._get_resource_type(name) == "raw":
            return name
        return os.path.splitext(name)[0]

    def _upload(self, name, content):
        return cloudinary.uploader.upload(
            content,
            public_id=self._public_id(name),
            resource_type=self._get_resource_type(name),
            type=DELIVERY_TYPE,
            overwrite=False,
            tags=self.TAG,
        )

    def _save(self, name, content):
        name = self._normalise_name(name)
        self._upload(name, UploadedFile(content, name))
        return name  # keep the extension so we know the file type later

    def _download_url(self, name):
        # A signed, short-lived API download link (works for images and raw PDFs alike).
        # It is only ever requested by the server, never sent to the browser.
        resource_type = self._get_resource_type(name)
        fmt = os.path.splitext(name)[1].lstrip(".") if resource_type == "image" else ""
        return cloudinary.utils.private_download_url(
            self._public_id(name), fmt, resource_type=resource_type, type=DELIVERY_TYPE,
        )

    def _open(self, name, mode="rb"):
        response = requests.get(self._download_url(name), timeout=20)
        if response.status_code == 404:
            raise FileNotFoundError(name)
        response.raise_for_status()
        return ContentFile(response.content, name=name)

    def url(self, name):
        raise NotImplementedError("Uploads are private. Serve them through the upload view.")

    def exists(self, name):
        return False  # file names contain a random part, so they never clash

    def size(self, name):
        return None

    def delete(self, name):
        cloudinary.uploader.destroy(
            self._public_id(name), resource_type=self._get_resource_type(name),
            type=DELIVERY_TYPE, invalidate=True,
        )
