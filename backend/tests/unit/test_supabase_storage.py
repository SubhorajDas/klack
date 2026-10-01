"""Supabase uploads stay immutable, bounded, private, and safe on provider failure."""

import json
from unittest.mock import MagicMock

import pytest
from pydantic import SecretStr, ValidationError

from klack.core.config import AppEnvironment, Settings
from klack.modules.files.domain import FileError
from klack.modules.files.storage import FileStorage


@pytest.fixture
def configured(settings):
    return settings.model_copy(
        update={
            "files_storage": "supabase",
            "files_s3_bucket": "private",
            "supabase_url": "https://project.supabase.co",
            "supabase_service_role_key": SecretStr("private-provider-key"),
            "files_max_bytes": 10,
        }
    )


@pytest.fixture
def connection(monkeypatch):
    client = MagicMock()
    response = client.getresponse.return_value
    response.status = 200
    response.read.return_value = b"hello"
    monkeypatch.setattr(
        "klack.modules.files.supabase_storage.HTTPSConnection", MagicMock(return_value=client)
    )
    return client


def test_native_storage_uses_create_only_and_authenticated_reads(configured, connection):
    store = FileStorage(configured)
    store.put("workspace/object", b"hello")
    args, kwargs = connection.request.call_args
    assert args == ("POST", "/storage/v1/object/private/workspace/object")
    assert kwargs["headers"]["x-upsert"] == "false"
    assert kwargs["headers"]["Authorization"] == "Bearer private-provider-key"
    assert kwargs["body"] == b"hello"
    assert store.get("workspace/object") == b"hello"
    assert connection.request.call_args.args == (
        "GET",
        "/storage/v1/object/authenticated/private/workspace/object",
    )
    connection.getresponse.return_value.read.assert_called_with(11)
    store.delete("workspace/object")
    assert json.loads(connection.request.call_args.kwargs["body"]) == {
        "prefixes": ["workspace/object"]
    }
    assert connection.close.call_count == 3


@pytest.mark.parametrize("method", ["put", "get", "delete"])
@pytest.mark.parametrize("status", [400, 401, 302, 500])
def test_provider_failure_is_redacted(configured, connection, method, status):
    connection.getresponse.return_value.status = status
    connection.getresponse.return_value.read.return_value = b"private-provider-key and object data"
    store = FileStorage(configured)
    with pytest.raises(FileError) as failure:
        getattr(store, method)("workspace/object", *([b"hello"] if method == "put" else []))
    assert failure.value.status == 503
    assert "private-provider-key" not in str(failure.value)
    assert connection.close.call_count == 1


def test_oversized_download_is_rejected(configured, connection):
    connection.getresponse.return_value.read.return_value = b"x" * 11
    with pytest.raises(FileError) as failure:
        FileStorage(configured).get("object")
    assert failure.value.status == 413


def test_network_failure_is_redacted(configured, connection):
    connection.request.side_effect = OSError("private-provider-key")
    with pytest.raises(FileError, match="unavailable") as failure:
        FileStorage(configured).put("object", b"hello")
    assert "private-provider-key" not in str(failure.value)
    assert failure.value.__suppress_context__
    assert connection.close.call_count == 1


@pytest.mark.parametrize("key", ["", "../escape", "a//b", "a\\b"])
def test_invalid_key_rejected_before_network_access(configured, connection, key):
    with pytest.raises(ValueError, match="Invalid storage key"):
        FileStorage(configured).delete(key)
    connection.request.assert_not_called()


def test_supabase_settings_require_origin_secret_and_production_scanner(configured):
    values = configured.model_dump()
    assert Settings.model_validate(values).files_storage == "supabase"
    assert "private-provider-key" not in repr(configured)
    for origin in ["http://project.supabase.co", "https://user:pass@host", "https://host/rest/v1"]:
        with pytest.raises(ValidationError, match="SUPABASE_URL"):
            Settings.model_validate({**values, "supabase_url": origin})
    with pytest.raises(ValidationError, match="SUPABASE_SERVICE_ROLE_KEY"):
        Settings.model_validate({**values, "supabase_service_role_key": ""})
    values.update(
        app_env=AppEnvironment.PRODUCTION,
        auth_cookie_secure=True,
        auth_public_web_origin="https://klack.example",
        auth_trusted_origin="https://klack.example",
    )
    with pytest.raises(ValidationError, match="scanner"):
        Settings.model_validate(values)
    assert Settings.model_validate({**values, "files_scan_required": False}).files_enabled
    assert Settings.model_validate({**values, "files_scan_host": "scanner"}).files_enabled
