"""HTTP contracts for durable channel messaging."""

from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from fastapi import FastAPI
from httpx import AsyncClient, Response

from klack.modules.identity.api.dependencies import get_identity_service
from klack.modules.identity.domain.entities import AuthenticatedIdentity, AuthSession, User
from klack.modules.identity.domain.errors import AuthenticationRequired, CsrfValidationFailed
from klack.modules.messaging.api.dependencies import get_message_service
from klack.modules.messaging.application.service import MessagePage
from klack.modules.messaging.domain.entities import Message
from klack.modules.messaging.domain.errors import (
    InvalidMessageBody,
    InvalidMessageCursor,
    MessageDeleted,
    MessageNotFound,
    MessagePermissionDenied,
)

NOW = datetime(2026, 9, 11, 18, 0, tzinfo=UTC)
USER_ID = UUID(int=1)
WORKSPACE_ID = UUID(int=10)
CHANNEL_ID = UUID(int=20)
MESSAGE_ID = UUID(int=30)
OLDER_ID = UUID(int=31)
SESSION_ID = UUID(int=40)
ACCESS_TOKEN = "message-access"
CSRF_TOKEN = "message-csrf"


def make_message(message_id: UUID = MESSAGE_ID, *, body: str | None = "hello") -> Message:
    return Message(
        id=message_id,
        workspace_id=WORKSPACE_ID,
        channel_id=CHANNEL_ID,
        author_user_id=USER_ID,
        body=body,
        created_at=NOW,
        edited_at=None,
        deleted_at=NOW if body is None else None,
    )


class FakeIdentityService:
    def __init__(self) -> None:
        self.identity = AuthenticatedIdentity(
            user=User(
                id=USER_ID,
                email="message-api@example.com",
                email_verified_at=None,
                created_at=NOW - timedelta(days=1),
                disabled_at=None,
            ),
            session=AuthSession(
                id=SESSION_ID,
                user_id=USER_ID,
                csrf_token_hash="digest",
                created_at=NOW - timedelta(hours=1),
                last_seen_at=NOW,
                expires_at=NOW + timedelta(days=1),
                revoked_at=None,
                revocation_reason=None,
                created_ip="192.0.2.1",
                last_ip="192.0.2.1",
                user_agent="Message API test",
            ),
        )

    async def authenticate_access(self, raw_access_token: str | None) -> AuthenticatedIdentity:
        if raw_access_token != ACCESS_TOKEN:
            raise AuthenticationRequired
        return self.identity

    def require_csrf(
        self,
        *,
        identity: AuthenticatedIdentity,
        csrf_token: str | None,
    ) -> None:
        assert identity == self.identity
        if csrf_token != CSRF_TOKEN:
            raise CsrfValidationFailed


class FakeMessageService:
    def __init__(self) -> None:
        self.message = make_message()
        self.calls: defaultdict[str, list[dict[str, object]]] = defaultdict(list)
        self.errors: dict[str, Exception] = {}

    def _record(self, operation: str, **kwargs: object) -> None:
        self.calls[operation].append(kwargs)
        error = self.errors.get(operation)
        if error is not None:
            raise error

    async def create_message(self, **kwargs: object) -> Message:
        self._record("create_message", **kwargs)
        return self.message

    async def list_messages(self, **kwargs: object) -> MessagePage:
        self._record("list_messages", **kwargs)
        return MessagePage(messages=[self.message], next_before=OLDER_ID)

    async def edit_message(self, **kwargs: object) -> Message:
        self._record("edit_message", **kwargs)
        return Message(
            id=MESSAGE_ID,
            workspace_id=WORKSPACE_ID,
            channel_id=CHANNEL_ID,
            author_user_id=USER_ID,
            body="edited",
            created_at=NOW,
            edited_at=NOW + timedelta(minutes=1),
            deleted_at=None,
        )

    async def delete_message(self, **kwargs: object) -> None:
        self._record("delete_message", **kwargs)


@dataclass
class MessageApiHarness:
    client: AsyncClient
    service: FakeMessageService

    def authenticate(self) -> None:
        self.client.cookies.set("klack_access", ACCESS_TOKEN)

    def mutation_headers(self) -> dict[str, str]:
        self.client.cookies.set("klack_csrf", CSRF_TOKEN)
        return {"Origin": "http://test", "X-CSRF-Token": CSRF_TOKEN}


@pytest.fixture
def message_api(app: FastAPI, client: AsyncClient) -> MessageApiHarness:
    identity = FakeIdentityService()
    service = FakeMessageService()
    app.dependency_overrides[get_identity_service] = lambda: identity
    app.dependency_overrides[get_message_service] = lambda: service
    yield MessageApiHarness(client=client, service=service)
    app.dependency_overrides.clear()


def assert_no_store(response: Response) -> None:
    assert response.headers["cache-control"] == "no-store"


async def test_message_routes_map_requests_and_responses(message_api: MessageApiHarness) -> None:
    message_api.authenticate()
    headers = message_api.mutation_headers()
    base = f"/api/v1/workspaces/{WORKSPACE_ID}/channels/{CHANNEL_ID}/messages"

    created = await message_api.client.post(base, headers=headers, json={"body": "hello"})
    assert created.status_code == 201
    assert created.json()["id"] == str(MESSAGE_ID)
    assert created.json()["body"] == "hello"
    assert_no_store(created)

    listed = await message_api.client.get(f"{base}?before={OLDER_ID}&limit=25")
    assert listed.status_code == 200
    assert listed.json()["next_before"] == str(OLDER_ID)
    assert len(listed.json()["messages"]) == 1
    assert_no_store(listed)

    edited = await message_api.client.patch(
        f"{base}/{MESSAGE_ID}",
        headers=headers,
        json={"body": "edited"},
    )
    assert edited.status_code == 200
    assert edited.json()["body"] == "edited"
    assert edited.json()["edited_at"] is not None
    assert_no_store(edited)

    deleted = await message_api.client.delete(f"{base}/{MESSAGE_ID}", headers=headers)
    assert deleted.status_code == 204
    assert deleted.content == b""
    assert_no_store(deleted)

    common = {
        "actor_user_id": USER_ID,
        "workspace_id": WORKSPACE_ID,
        "channel_id": CHANNEL_ID,
    }
    assert message_api.service.calls["create_message"] == [{**common, "body": "hello"}]
    assert message_api.service.calls["list_messages"] == [
        {**common, "before": OLDER_ID, "limit": 25},
    ]
    assert message_api.service.calls["edit_message"] == [
        {**common, "message_id": MESSAGE_ID, "body": "edited"},
    ]
    assert message_api.service.calls["delete_message"] == [
        {**common, "message_id": MESSAGE_ID},
    ]


async def test_routes_require_authentication_and_mutations_require_csrf(
    message_api: MessageApiHarness,
) -> None:
    base = f"/api/v1/workspaces/{WORKSPACE_ID}/channels/{CHANNEL_ID}/messages"
    unauthenticated = await message_api.client.get(base)
    assert unauthenticated.status_code == 401
    assert_no_store(unauthenticated)
    message_api.authenticate()
    missing_csrf = await message_api.client.post(
        base,
        headers={"Origin": "http://test"},
        json={"body": "hello"},
    )
    assert missing_csrf.status_code == 403
    assert missing_csrf.json()["code"] == "csrf_validation_failed"
    assert message_api.service.calls == {}


@pytest.mark.parametrize("body", ["", "x" * 4_001])
async def test_api_rejects_invalid_bounded_body(
    message_api: MessageApiHarness,
    body: str,
) -> None:
    message_api.authenticate()
    response = await message_api.client.post(
        f"/api/v1/workspaces/{WORKSPACE_ID}/channels/{CHANNEL_ID}/messages",
        headers=message_api.mutation_headers(),
        json={"body": body},
    )
    assert response.status_code == 422
    assert response.json()["code"] == "validation_error"
    assert message_api.service.calls == {}


async def test_history_query_validation_is_bounded(message_api: MessageApiHarness) -> None:
    message_api.authenticate()
    base = f"/api/v1/workspaces/{WORKSPACE_ID}/channels/{CHANNEL_ID}/messages"
    assert (await message_api.client.get(f"{base}?limit=0")).status_code == 422
    assert (await message_api.client.get(f"{base}?limit=101")).status_code == 422
    assert (await message_api.client.get(f"{base}?before=not-a-uuid")).status_code == 422


@pytest.mark.parametrize(
    ("error", "status", "code"),
    [
        (InvalidMessageBody(), 422, "validation_error"),
        (InvalidMessageCursor(), 422, "invalid_message_cursor"),
        (MessageNotFound(), 404, "message_not_found"),
        (MessagePermissionDenied(), 403, "message_permission_denied"),
        (MessageDeleted(), 409, "message_deleted"),
    ],
)
async def test_expected_message_errors_use_problem_details(
    message_api: MessageApiHarness,
    error: Exception,
    status: int,
    code: str,
) -> None:
    message_api.authenticate()
    message_api.service.errors["list_messages"] = error
    response = await message_api.client.get(
        f"/api/v1/workspaces/{WORKSPACE_ID}/channels/{CHANNEL_ID}/messages",
    )
    assert response.status_code == status
    assert response.json()["code"] == code
    assert response.headers["content-type"] == "application/problem+json"
    assert_no_store(response)
