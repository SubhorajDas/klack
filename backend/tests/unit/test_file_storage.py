"""Private storage immutability and scanner failures must not release unsafe files."""

import io
import socket
from unittest.mock import MagicMock

import pytest
from pydantic import ValidationError

from klack.core.config import AppEnvironment, Settings
from klack.modules.files.domain import FileError
from klack.modules.files.storage import FileStorage, inspect_content


def test_local_storage_is_private_immutable_and_bounded(settings, tmp_path):
    store = FileStorage(settings.model_copy(update={"files_local_path": str(tmp_path)}))
    store.put("workspace/object", b"content")
    assert store.get("workspace/object") == b"content"
    with pytest.raises(FileExistsError):
        store.put("workspace/object", b"replacement")
    with pytest.raises(ValueError, match="Invalid storage key"):
        store.get("../escape")
    store.delete("workspace/object")
    store.delete("workspace/object")


def test_s3_storage_uses_private_immutable_keys(settings, monkeypatch):
    client = MagicMock()
    factory = MagicMock(return_value=client)
    monkeypatch.setattr("klack.modules.files.storage.boto3.client", factory)
    configured = settings.model_copy(update={"files_storage": "s3", "files_s3_bucket": "private"})
    store = FileStorage(configured)
    store.put("key", b"hello")
    client.put_object.assert_called_once_with(
        Bucket="private",
        Key="key",
        Body=b"hello",
        ContentType="application/octet-stream",
        IfNoneMatch="*",
    )
    client.get_object.return_value = {"Body": io.BytesIO(b"hello")}
    assert store.get("key") == b"hello"
    store.delete("key")
    client.delete_object.assert_called_once_with(Bucket="private", Key="key")
    assert client.close.call_count == 3


@pytest.mark.parametrize(
    "reply", [b"stream: Eicar FOUND\0", b"stream: size limit ERROR\0", b"", b"malformed\0"]
)
def test_scanning_fails_closed(settings, monkeypatch, reply):
    scanner = MagicMock()
    scanner.__enter__.return_value = scanner
    scanner.recv.return_value = reply
    monkeypatch.setattr(socket, "create_connection", lambda *args, **kwargs: scanner)
    with pytest.raises(FileError, match="safety scan"):
        inspect_content(b"test", settings.model_copy(update={"files_scan_host": "scanner"}))


def test_scanner_streaming_protocol_and_unavailability(settings, monkeypatch):
    scanner = MagicMock()
    scanner.__enter__.return_value = scanner
    scanner.recv.side_effect = [b"stream:", b" OK\0"]
    monkeypatch.setattr(socket, "create_connection", lambda *args, **kwargs: scanner)
    configured = settings.model_copy(update={"files_scan_host": "scanner"})
    assert inspect_content(b"<svg onload='alert(1)'/>", configured) == "application/octet-stream"
    assert scanner.sendall.call_args_list[0].args == (b"zINSTREAM\0",)
    assert scanner.sendall.call_args_list[-1].args == (b"\0\0\0\0",)
    monkeypatch.setattr(socket, "create_connection", MagicMock(side_effect=OSError))
    with pytest.raises(FileError, match="unavailable") as failure:
        inspect_content(b"test", configured)
    assert failure.value.status == 503


def test_production_requires_scanner_and_private_storage(settings):
    values = settings.model_dump()
    values.update(
        app_env=AppEnvironment.PRODUCTION,
        auth_trusted_origin="https://test.example",
        auth_public_web_origin="https://test.example",
        auth_cookie_secure=True,
    )
    with pytest.raises(ValidationError, match="requires S3"):
        Settings.model_validate(values)
    values.update(files_storage="s3")
    with pytest.raises(ValidationError, match="FILES_S3_BUCKET"):
        Settings.model_validate(values)
    values.update(files_s3_bucket="private", files_scan_host="scanner")
    assert Settings.model_validate(values).files_enabled
