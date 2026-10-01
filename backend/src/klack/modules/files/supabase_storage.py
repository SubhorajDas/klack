"""Private Supabase Storage REST access with atomic create-only uploads."""

import json
from contextlib import closing
from http.client import HTTPException, HTTPSConnection
from urllib.parse import quote, urlsplit

from klack.core.config import Settings
from klack.modules.files.domain import FileError


class SupabaseFileStorage:
    """Use native uploads because Supabase S3 ignores conditional PutObject headers."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    def _request(
        self,
        method: str,
        path: str,
        *,
        body: bytes | None = None,
        content_type: str = "application/octet-stream",
        max_response_bytes: int = 4096,
    ) -> tuple[int, bytes]:
        origin = urlsplit(self._settings.supabase_url)
        if origin.hostname is None:
            raise ValueError("SUPABASE_URL must contain a hostname")
        secret = self._settings.supabase_service_role_key.get_secret_value()
        headers = {
            "Authorization": f"Bearer {secret}",
            "apikey": secret,
            "Content-Type": content_type,
            "x-upsert": "false",
        }
        # HTTPSConnection verifies TLS and does not follow redirects with credentials.
        try:
            with closing(HTTPSConnection(origin.hostname, origin.port, timeout=30)) as client:
                client.request(method, "/storage/v1" + path, body=body, headers=headers)
                with closing(client.getresponse()) as response:
                    return response.status, response.read(max_response_bytes + 1)
        except (OSError, HTTPException):
            raise FileError("File storage is unavailable. Please retry later.", 503) from None

    def _object_path(self, key: str, *, authenticated: bool = False) -> str:
        if not key or any(part in {"", ".", ".."} for part in key.split("/")) or "\\" in key:
            raise ValueError("Invalid storage key")
        bucket = quote(self._settings.files_s3_bucket, safe="")
        prefix = "/object/authenticated" if authenticated else "/object"
        return f"{prefix}/{bucket}/{quote(key, safe='/')}"

    def put(self, key: str, data: bytes) -> None:
        status, _ = self._request("POST", self._object_path(key), body=data)
        if not 200 <= status < 300:
            # Provider bodies may contain object names or request details; never expose them.
            raise FileError("The file could not be stored. Please retry.", 503)

    def get(self, key: str) -> bytes:
        status, data = self._request(
            "GET",
            self._object_path(key, authenticated=True),
            max_response_bytes=self._settings.files_max_bytes,
        )
        if not 200 <= status < 300:
            raise FileError("The file could not be downloaded. Please retry.", 503)
        if len(data) > self._settings.files_max_bytes:
            raise FileError("The stored file exceeds its size limit.", 413)
        return data

    def delete(self, key: str) -> None:
        self._object_path(key)
        bucket = quote(self._settings.files_s3_bucket, safe="")
        status, _ = self._request(
            "DELETE",
            f"/object/{bucket}",
            body=json.dumps({"prefixes": [key]}).encode("utf-8"),
            content_type="application/json",
        )
        # A successful bulk remove also accepts an already absent object.
        if not 200 <= status < 300:
            raise FileError("The file could not be removed. Please retry.", 503)
