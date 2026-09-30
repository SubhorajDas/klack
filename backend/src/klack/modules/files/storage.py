"""Private storage and fail-closed content inspection, run off the event loop."""

import io
import socket
import struct
import time
import warnings
from contextlib import closing
from pathlib import Path

import boto3
from botocore.config import Config
from PIL import Image, UnidentifiedImageError

from klack.core.config import Settings
from klack.modules.files.domain import FileError


class FileStorage:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def _path(self, key: str) -> Path:
        root = Path(self.settings.files_local_path).resolve()
        path = (root / key).resolve()
        if not path.is_relative_to(root) or path == root:
            raise ValueError("Invalid storage key")
        return path

    def put(self, key: str, data: bytes) -> None:
        if self.settings.files_storage == "s3":
            with closing(
                boto3.client(
                    "s3",
                    endpoint_url=self.settings.files_s3_endpoint or None,
                    config=Config(connect_timeout=5, read_timeout=30, retries={"max_attempts": 2}),
                )
            ) as client:
                client.put_object(
                    Bucket=self.settings.files_s3_bucket,
                    Key=key,
                    Body=data,
                    ContentType="application/octet-stream",
                    IfNoneMatch="*",
                )
        else:
            path = self._path(key)
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("xb") as target:
                target.write(data)

    def get(self, key: str) -> bytes:
        if self.settings.files_storage == "s3":
            with closing(
                boto3.client(
                    "s3",
                    endpoint_url=self.settings.files_s3_endpoint or None,
                    config=Config(connect_timeout=5, read_timeout=30, retries={"max_attempts": 2}),
                )
            ) as client:
                result = client.get_object(Bucket=self.settings.files_s3_bucket, Key=key)
                with result["Body"] as body:
                    return body.read(self.settings.files_max_bytes + 1)
        with self._path(key).open("rb") as source:
            return source.read(self.settings.files_max_bytes + 1)

    def delete(self, key: str) -> None:
        if self.settings.files_storage == "s3":
            with closing(
                boto3.client(
                    "s3",
                    endpoint_url=self.settings.files_s3_endpoint or None,
                    config=Config(connect_timeout=5, read_timeout=30, retries={"max_attempts": 2}),
                )
            ) as client:
                client.delete_object(Bucket=self.settings.files_s3_bucket, Key=key)
        else:
            self._path(key).unlink(missing_ok=True)


def inspect_content(data: bytes, settings: Settings) -> str:
    """Only validated raster images may render inline; all other bytes are downloads."""
    if settings.files_scan_host:
        try:
            deadline = time.monotonic() + 60
            with socket.create_connection(
                (settings.files_scan_host, settings.files_scan_port), timeout=30
            ) as scanner:
                scanner.sendall(b"zINSTREAM\0")
                for offset in range(0, len(data), 65536):
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise TimeoutError("Scan deadline exceeded")
                    scanner.settimeout(min(30, remaining))
                    chunk = data[offset : offset + 65536]
                    scanner.sendall(struct.pack("!I", len(chunk)) + chunk)
                scanner.sendall(struct.pack("!I", 0))
                reply = bytearray()
                while b"\0" not in reply and len(reply) < 4096:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise TimeoutError("Scan deadline exceeded")
                    scanner.settimeout(min(30, remaining))
                    chunk = scanner.recv(4096)
                    if not chunk:
                        break
                    reply.extend(chunk)
                if bytes(reply).strip(b"\0\r\n") != b"stream: OK":
                    raise FileError("The file did not pass the safety scan.")
        except OSError as exc:
            raise FileError("File scanning is unavailable. Please retry later.", 503) from exc
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(data)) as image:
                if image.width * image.height > 20_000_000:
                    return "application/octet-stream"
                image.verify()
                return {
                    "PNG": "image/png",
                    "JPEG": "image/jpeg",
                    "GIF": "image/gif",
                    "WEBP": "image/webp",
                }.get(image.format or "", "application/octet-stream")
    except (
        UnidentifiedImageError,
        OSError,
        SyntaxError,
        ValueError,
        Image.DecompressionBombError,
        Image.DecompressionBombWarning,
    ):
        return "application/octet-stream"
