"""HTTP contracts for channels and explicit channel memberships."""

from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from http import HTTPStatus
from uuid import UUID

import pytest
from fastapi import FastAPI
from httpx import AsyncClient, Response

from klack.modules.channels.api.dependencies import get_channel_service
from klack.modules.channels.application.service import ChannelView
from klack.modules.channels.domain.entities import (
    Channel,
    ChannelMembership,
    ChannelVisibility,
)
from klack.modules.channels.domain.errors import (
    ChannelArchived,
    ChannelError,
    ChannelMembershipNotFound,
    ChannelNameConflict,
    ChannelNotFound,
    ChannelPermissionDenied,
    InvalidChannelName,
    TargetWorkspaceMembershipNotFound,
)
from klack.modules.identity.api.dependencies import get_identity_service
from klack.modules.identity.domain.entities import AuthenticatedIdentity, AuthSession, User
from klack.modules.identity.domain.errors import AuthenticationRequired, CsrfValidationFailed

NOW = datetime(2026, 9, 11, 10, 30, tzinfo=UTC)
ACTOR_USER_ID = UUID("11111111-1111-4111-8111-111111111111")
TARGET_USER_ID = UUID("22222222-2222-4222-8222-222222222222")
WORKSPACE_ID = UUID("33333333-3333-4333-8333-333333333333")
CHANNEL_ID = UUID("44444444-4444-4444-8444-444444444444")
PRIVATE_CHANNEL_ID = UUID("55555555-5555-4555-8555-555555555555")
PRIMARY_SESSION_ID = UUID("66666666-6666-4666-8666-666666666666")
SECOND_SESSION_ID = UUID("77777777-7777-4777-8777-777777777777")
PRIMARY_ACCESS_TOKEN = "primary-access"
SECOND_ACCESS_TOKEN = "second-access"
PRIMARY_CSRF_TOKEN = "primary-session-csrf"
SECOND_CSRF_TOKEN = "second-session-csrf"
ORIGIN_HEADERS = {"Origin": "http://test"}


def timestamp(value: datetime) -> str:
    """Return the JSON datetime representation emitted by Pydantic."""
    return value.isoformat().replace("+00:00", "Z")


def make_user() -> User:
    return User(
        id=ACTOR_USER_ID,
        email="person@example.com",
        email_verified_at=None,
        created_at=NOW - timedelta(days=30),
        disabled_at=None,
    )


def make_session(session_id: UUID) -> AuthSession:
    return AuthSession(
        id=session_id,
        user_id=ACTOR_USER_ID,
        csrf_token_hash="digest-is-owned-by-the-session",
        created_at=NOW - timedelta(hours=1),
        last_seen_at=NOW,
        expires_at=NOW + timedelta(days=30),
        revoked_at=None,
        revocation_reason=None,
        created_ip="192.0.2.10",
        last_ip="192.0.2.10",
        user_agent="Channel API test",
    )


def make_channel(
    channel_id: UUID = CHANNEL_ID,
    *,
    name: str = "engineering",
    visibility: ChannelVisibility = ChannelVisibility.PUBLIC,
    updated_at: datetime = NOW,
    archived_at: datetime | None = None,
) -> Channel:
    return Channel(
        id=channel_id,
        workspace_id=WORKSPACE_ID,
        name=name,
        visibility=visibility,
        created_by_user_id=ACTOR_USER_ID,
        created_at=NOW - timedelta(minutes=5),
        updated_at=updated_at,
        archived_at=archived_at,
        archived_by_user_id=ACTOR_USER_ID if archived_at is not None else None,
    )


def make_membership(
    user_id: UUID = ACTOR_USER_ID,
    *,
    added_by_user_id: UUID = ACTOR_USER_ID,
) -> ChannelMembership:
    return ChannelMembership(
        workspace_id=WORKSPACE_ID,
        channel_id=CHANNEL_ID,
        user_id=user_id,
        added_by_user_id=added_by_user_id,
        joined_at=NOW + timedelta(minutes=1),
    )


class FakeIdentityService:
    """Authenticate cookies and model a CSRF digest bound to each session."""

    def __init__(self) -> None:
        self.primary_identity = AuthenticatedIdentity(
            user=make_user(),
            session=make_session(PRIMARY_SESSION_ID),
        )
        self.second_identity = AuthenticatedIdentity(
            user=make_user(),
            session=make_session(SECOND_SESSION_ID),
        )
        self.identities = {
            PRIMARY_ACCESS_TOKEN: self.primary_identity,
            SECOND_ACCESS_TOKEN: self.second_identity,
        }
        self.csrf_by_session = {
            PRIMARY_SESSION_ID: PRIMARY_CSRF_TOKEN,
            SECOND_SESSION_ID: SECOND_CSRF_TOKEN,
        }
        self.authenticate_calls: list[str | None] = []
        self.csrf_calls: list[dict[str, object]] = []

    async def authenticate_access(
        self,
        raw_access_token: str | None,
    ) -> AuthenticatedIdentity:
        self.authenticate_calls.append(raw_access_token)
        identity = self.identities.get(raw_access_token or "")
        if identity is None:
            raise AuthenticationRequired
        return identity

    def require_csrf(
        self,
        *,
        identity: AuthenticatedIdentity,
        csrf_token: str | None,
    ) -> None:
        self.csrf_calls.append({"identity": identity, "csrf_token": csrf_token})
        if csrf_token != self.csrf_by_session[identity.session.id]:
            raise CsrfValidationFailed


class FakeChannelService:
    """Record API-to-application calls and return deterministic domain values."""

    def __init__(self) -> None:
        self.channel = make_channel()
        self.private_channel = make_channel(
            PRIVATE_CHANNEL_ID,
            name="leadership",
            visibility=ChannelVisibility.PRIVATE,
        )
        self.updated_channel = make_channel(
            name="platform",
            visibility=ChannelVisibility.PRIVATE,
            updated_at=NOW + timedelta(minutes=10),
        )
        self.archived_channel = make_channel(
            updated_at=NOW + timedelta(minutes=20),
            archived_at=NOW + timedelta(minutes=20),
        )
        self.actor_membership = make_membership()
        self.target_membership = make_membership(TARGET_USER_ID)
        self.calls: defaultdict[str, list[dict[str, object]]] = defaultdict(list)
        self.errors: dict[str, Exception] = {}

    def _record(self, operation: str, **kwargs: object) -> None:
        self.calls[operation].append(kwargs)
        error = self.errors.get(operation)
        if error is not None:
            raise error

    async def create_channel(
        self,
        *,
        actor_user_id: UUID,
        workspace_id: UUID,
        name: str,
        visibility: ChannelVisibility,
    ) -> ChannelView:
        self._record(
            "create_channel",
            actor_user_id=actor_user_id,
            workspace_id=workspace_id,
            name=name,
            visibility=visibility,
        )
        return ChannelView(channel=self.channel, is_member=True)

    async def list_channels(
        self,
        *,
        actor_user_id: UUID,
        workspace_id: UUID,
        include_archived: bool = False,
    ) -> list[ChannelView]:
        self._record(
            "list_channels",
            actor_user_id=actor_user_id,
            workspace_id=workspace_id,
            include_archived=include_archived,
        )
        return [
            ChannelView(channel=self.channel, is_member=True),
            ChannelView(channel=self.private_channel, is_member=False),
        ]

    async def get_channel(
        self,
        *,
        actor_user_id: UUID,
        workspace_id: UUID,
        channel_id: UUID,
    ) -> ChannelView:
        self._record(
            "get_channel",
            actor_user_id=actor_user_id,
            workspace_id=workspace_id,
            channel_id=channel_id,
        )
        return ChannelView(channel=self.channel, is_member=True)

    async def update_channel(
        self,
        *,
        actor_user_id: UUID,
        workspace_id: UUID,
        channel_id: UUID,
        name: str | None,
        visibility: ChannelVisibility | None,
    ) -> ChannelView:
        self._record(
            "update_channel",
            actor_user_id=actor_user_id,
            workspace_id=workspace_id,
            channel_id=channel_id,
            name=name,
            visibility=visibility,
        )
        return ChannelView(channel=self.updated_channel, is_member=True)

    async def archive_channel(
        self,
        *,
        actor_user_id: UUID,
        workspace_id: UUID,
        channel_id: UUID,
    ) -> ChannelView:
        self._record(
            "archive_channel",
            actor_user_id=actor_user_id,
            workspace_id=workspace_id,
            channel_id=channel_id,
        )
        return ChannelView(channel=self.archived_channel, is_member=True)

    async def unarchive_channel(
        self,
        *,
        actor_user_id: UUID,
        workspace_id: UUID,
        channel_id: UUID,
    ) -> ChannelView:
        self._record(
            "unarchive_channel",
            actor_user_id=actor_user_id,
            workspace_id=workspace_id,
            channel_id=channel_id,
        )
        return ChannelView(channel=self.channel, is_member=True)

    async def list_memberships(
        self,
        *,
        actor_user_id: UUID,
        workspace_id: UUID,
        channel_id: UUID,
    ) -> list[ChannelMembership]:
        self._record(
            "list_memberships",
            actor_user_id=actor_user_id,
            workspace_id=workspace_id,
            channel_id=channel_id,
        )
        return [self.actor_membership, self.target_membership]

    async def get_current_membership(
        self,
        *,
        actor_user_id: UUID,
        workspace_id: UUID,
        channel_id: UUID,
    ) -> ChannelMembership:
        self._record(
            "get_current_membership",
            actor_user_id=actor_user_id,
            workspace_id=workspace_id,
            channel_id=channel_id,
        )
        return self.actor_membership

    async def join_channel(
        self,
        *,
        actor_user_id: UUID,
        workspace_id: UUID,
        channel_id: UUID,
    ) -> ChannelMembership:
        self._record(
            "join_channel",
            actor_user_id=actor_user_id,
            workspace_id=workspace_id,
            channel_id=channel_id,
        )
        return self.actor_membership

    async def leave_channel(
        self,
        *,
        actor_user_id: UUID,
        workspace_id: UUID,
        channel_id: UUID,
    ) -> None:
        self._record(
            "leave_channel",
            actor_user_id=actor_user_id,
            workspace_id=workspace_id,
            channel_id=channel_id,
        )

    async def add_membership(
        self,
        *,
        actor_user_id: UUID,
        workspace_id: UUID,
        channel_id: UUID,
        target_user_id: UUID,
    ) -> ChannelMembership:
        self._record(
            "add_membership",
            actor_user_id=actor_user_id,
            workspace_id=workspace_id,
            channel_id=channel_id,
            target_user_id=target_user_id,
        )
        return self.target_membership

    async def remove_membership(
        self,
        *,
        actor_user_id: UUID,
        workspace_id: UUID,
        channel_id: UUID,
        target_user_id: UUID,
    ) -> None:
        self._record(
            "remove_membership",
            actor_user_id=actor_user_id,
            workspace_id=workspace_id,
            channel_id=channel_id,
            target_user_id=target_user_id,
        )


@dataclass(frozen=True)
class ChannelApiHarness:
    client: AsyncClient
    identity_service: FakeIdentityService
    channel_service: FakeChannelService

    def mutation_headers(self, csrf_token: str = PRIMARY_CSRF_TOKEN) -> dict[str, str]:
        return {**ORIGIN_HEADERS, "X-CSRF-Token": csrf_token}


@pytest.fixture
def channel_api(app: FastAPI, client: AsyncClient) -> ChannelApiHarness:
    identity_service = FakeIdentityService()
    channel_service = FakeChannelService()
    app.dependency_overrides[get_identity_service] = lambda: identity_service
    app.dependency_overrides[get_channel_service] = lambda: channel_service
    client.cookies.set("klack_access", PRIMARY_ACCESS_TOKEN)
    client.cookies.set("klack_csrf", PRIMARY_CSRF_TOKEN)
    return ChannelApiHarness(
        client=client,
        identity_service=identity_service,
        channel_service=channel_service,
    )


def expected_channel(view: ChannelView) -> dict[str, object]:
    channel = view.channel
    return {
        "id": str(channel.id),
        "workspace_id": str(channel.workspace_id),
        "name": channel.name,
        "visibility": channel.visibility.value,
        "created_by_user_id": str(channel.created_by_user_id),
        "created_at": timestamp(channel.created_at),
        "updated_at": timestamp(channel.updated_at),
        "archived_at": timestamp(channel.archived_at) if channel.archived_at else None,
        "archived_by_user_id": (
            str(channel.archived_by_user_id) if channel.archived_by_user_id else None
        ),
        "is_member": view.is_member,
    }


def expected_membership(membership: ChannelMembership) -> dict[str, str]:
    return {
        "workspace_id": str(membership.workspace_id),
        "channel_id": str(membership.channel_id),
        "user_id": str(membership.user_id),
        "added_by_user_id": str(membership.added_by_user_id),
        "joined_at": timestamp(membership.joined_at),
    }


def assert_no_store(response: Response) -> None:
    assert response.headers["cache-control"] == "no-store"


async def test_channel_routes_expose_stable_shapes_and_actor_calls(
    channel_api: ChannelApiHarness,
) -> None:
    service = channel_api.channel_service
    client = channel_api.client
    base_path = f"/api/v1/workspaces/{WORKSPACE_ID}/channels"

    created = await client.post(
        base_path,
        headers=channel_api.mutation_headers(),
        json={"name": "engineering"},
    )
    listed = await client.get(base_path)
    listed_with_archived = await client.get(f"{base_path}?include_archived=true")
    loaded = await client.get(f"{base_path}/{CHANNEL_ID}")
    updated = await client.patch(
        f"{base_path}/{CHANNEL_ID}",
        headers=channel_api.mutation_headers(),
        json={"name": "platform", "visibility": "private"},
    )
    archived = await client.post(
        f"{base_path}/{CHANNEL_ID}/archive",
        headers=channel_api.mutation_headers(),
    )
    unarchived = await client.post(
        f"{base_path}/{CHANNEL_ID}/unarchive",
        headers=channel_api.mutation_headers(),
    )

    assert created.status_code == 201
    assert created.json() == expected_channel(ChannelView(channel=service.channel, is_member=True))
    expected_list = {
        "channels": [
            expected_channel(ChannelView(channel=service.channel, is_member=True)),
            expected_channel(ChannelView(channel=service.private_channel, is_member=False)),
        ],
    }
    assert listed.status_code == 200
    assert listed.json() == expected_list
    assert listed_with_archived.status_code == 200
    assert listed_with_archived.json() == expected_list
    assert loaded.status_code == 200
    assert loaded.json() == expected_channel(ChannelView(channel=service.channel, is_member=True))
    assert updated.status_code == 200
    assert updated.json() == expected_channel(
        ChannelView(channel=service.updated_channel, is_member=True),
    )
    assert archived.status_code == 200
    assert archived.json() == expected_channel(
        ChannelView(channel=service.archived_channel, is_member=True),
    )
    assert unarchived.status_code == 200
    assert unarchived.json() == expected_channel(
        ChannelView(channel=service.channel, is_member=True),
    )
    for response in (
        created,
        listed,
        listed_with_archived,
        loaded,
        updated,
        archived,
        unarchived,
    ):
        assert_no_store(response)

    common = {"actor_user_id": ACTOR_USER_ID, "workspace_id": WORKSPACE_ID}
    channel_target = {**common, "channel_id": CHANNEL_ID}
    assert service.calls["create_channel"] == [
        {
            **common,
            "name": "engineering",
            "visibility": ChannelVisibility.PUBLIC,
        },
    ]
    assert service.calls["list_channels"] == [
        {**common, "include_archived": False},
        {**common, "include_archived": True},
    ]
    assert service.calls["get_channel"] == [channel_target]
    assert service.calls["update_channel"] == [
        {
            **channel_target,
            "name": "platform",
            "visibility": ChannelVisibility.PRIVATE,
        },
    ]
    assert service.calls["archive_channel"] == [channel_target]
    assert service.calls["unarchive_channel"] == [channel_target]


async def test_channel_membership_routes_expose_shapes_and_actor_calls(
    channel_api: ChannelApiHarness,
) -> None:
    service = channel_api.channel_service
    client = channel_api.client
    base_path = f"/api/v1/workspaces/{WORKSPACE_ID}/channels/{CHANNEL_ID}/memberships"

    listed = await client.get(base_path)
    added = await client.put(
        f"{base_path}/{TARGET_USER_ID}",
        headers=channel_api.mutation_headers(),
    )
    removed = await client.delete(
        f"{base_path}/{TARGET_USER_ID}",
        headers=channel_api.mutation_headers(),
    )

    assert listed.status_code == 200
    assert listed.json() == {
        "memberships": [
            expected_membership(service.actor_membership),
            expected_membership(service.target_membership),
        ],
    }
    assert added.status_code == 200
    assert added.json() == expected_membership(service.target_membership)
    assert removed.status_code == 204
    assert removed.content == b""
    for response in (listed, added, removed):
        assert_no_store(response)

    common = {
        "actor_user_id": ACTOR_USER_ID,
        "workspace_id": WORKSPACE_ID,
        "channel_id": CHANNEL_ID,
    }
    assert service.calls["list_memberships"] == [common]
    assert service.calls["add_membership"] == [
        {**common, "target_user_id": TARGET_USER_ID},
    ]
    assert service.calls["remove_membership"] == [
        {**common, "target_user_id": TARGET_USER_ID},
    ]


async def test_memberships_me_routes_win_before_the_uuid_member_route(
    channel_api: ChannelApiHarness,
) -> None:
    service = channel_api.channel_service
    client = channel_api.client
    path = f"/api/v1/workspaces/{WORKSPACE_ID}/channels/{CHANNEL_ID}/memberships/me"

    current = await client.get(path)
    joined = await client.put(path, headers=channel_api.mutation_headers())
    left = await client.delete(path, headers=channel_api.mutation_headers())

    assert current.status_code == 200
    assert current.json() == expected_membership(service.actor_membership)
    assert joined.status_code == 200
    assert joined.json() == expected_membership(service.actor_membership)
    assert left.status_code == 204
    assert left.content == b""
    for response in (current, joined, left):
        assert_no_store(response)

    common = {
        "actor_user_id": ACTOR_USER_ID,
        "workspace_id": WORKSPACE_ID,
        "channel_id": CHANNEL_ID,
    }
    assert service.calls["get_current_membership"] == [common]
    assert service.calls["join_channel"] == [common]
    assert service.calls["leave_channel"] == [common]
    assert service.calls["add_membership"] == []
    assert service.calls["remove_membership"] == []


async def test_channel_names_are_trimmed_before_request_length_validation(
    channel_api: ChannelApiHarness,
) -> None:
    raw_name = f"  {'x' * 80}  "
    base_path = f"/api/v1/workspaces/{WORKSPACE_ID}/channels"

    created = await channel_api.client.post(
        base_path,
        headers=channel_api.mutation_headers(),
        json={"name": raw_name},
    )
    updated = await channel_api.client.patch(
        f"{base_path}/{CHANNEL_ID}",
        headers=channel_api.mutation_headers(),
        json={"name": raw_name},
    )

    assert created.status_code == 201
    assert updated.status_code == 200
    common = {"actor_user_id": ACTOR_USER_ID, "workspace_id": WORKSPACE_ID}
    assert channel_api.channel_service.calls["create_channel"] == [
        {
            **common,
            "name": "x" * 80,
            "visibility": ChannelVisibility.PUBLIC,
        },
    ]
    assert channel_api.channel_service.calls["update_channel"] == [
        {
            **common,
            "channel_id": CHANNEL_ID,
            "name": "x" * 80,
            "visibility": None,
        },
    ]


async def test_channel_payloads_are_strict_and_patch_requires_a_change(
    channel_api: ChannelApiHarness,
) -> None:
    client = channel_api.client
    base_path = f"/api/v1/workspaces/{WORKSPACE_ID}/channels"

    invalid_create = await client.post(
        base_path,
        headers=channel_api.mutation_headers(),
        json={"name": "", "unexpected": "private-create-value"},
    )
    invalid_visibility = await client.post(
        base_path,
        headers=channel_api.mutation_headers(),
        json={"name": "engineering", "visibility": "secret"},
    )
    empty_patch = await client.patch(
        f"{base_path}/{CHANNEL_ID}",
        headers=channel_api.mutation_headers(),
        json={},
    )
    null_patch = await client.patch(
        f"{base_path}/{CHANNEL_ID}",
        headers=channel_api.mutation_headers(),
        json={"name": None, "visibility": None},
    )
    extra_patch = await client.patch(
        f"{base_path}/{CHANNEL_ID}",
        headers=channel_api.mutation_headers(),
        json={"visibility": "public", "note": "private-patch-value"},
    )

    for response in (
        invalid_create,
        invalid_visibility,
        empty_patch,
        null_patch,
        extra_patch,
    ):
        assert response.status_code == 422
        assert response.headers["content-type"].startswith("application/problem+json")
        assert response.json()["code"] == "validation_error"
        assert response.json()["request_id"] == response.headers["x-request-id"]
        assert_no_store(response)
    assert set(invalid_create.json()["field_errors"]) == {"name", "unexpected"}
    assert set(invalid_visibility.json()["field_errors"]) == {"visibility"}
    assert set(empty_patch.json()["field_errors"]) == {"request"}
    assert set(null_patch.json()["field_errors"]) == {"request"}
    assert set(extra_patch.json()["field_errors"]) == {"note"}
    assert "private-create-value" not in invalid_create.text
    assert "private-patch-value" not in extra_patch.text
    assert channel_api.channel_service.calls == {}


class UnmappedChannelError(ChannelError):
    """Exercise the safe fallback for a future expected channel failure."""


@dataclass(frozen=True)
class ChannelErrorCase:
    operation: str
    method: str
    path: str
    payload: dict[str, str] | None
    error: Exception
    status: int
    code: str
    detail: str


ERROR_CASES = [
    ChannelErrorCase(
        "create_channel",
        "POST",
        f"/api/v1/workspaces/{WORKSPACE_ID}/channels",
        {"name": "engineering"},
        InvalidChannelName(),
        422,
        "validation_error",
        "The channel name is invalid.",
    ),
    ChannelErrorCase(
        "get_channel",
        "GET",
        f"/api/v1/workspaces/{WORKSPACE_ID}/channels/{CHANNEL_ID}",
        None,
        ChannelNotFound(),
        404,
        "channel_not_found",
        "The channel was not found.",
    ),
    ChannelErrorCase(
        "update_channel",
        "PATCH",
        f"/api/v1/workspaces/{WORKSPACE_ID}/channels/{CHANNEL_ID}",
        {"visibility": "private"},
        ChannelPermissionDenied(),
        403,
        "channel_permission_denied",
        "The current membership cannot perform this operation.",
    ),
    ChannelErrorCase(
        "create_channel",
        "POST",
        f"/api/v1/workspaces/{WORKSPACE_ID}/channels",
        {"name": "engineering"},
        ChannelNameConflict(),
        409,
        "channel_name_conflict",
        "A channel with this name already exists in the workspace.",
    ),
    ChannelErrorCase(
        "get_current_membership",
        "GET",
        (f"/api/v1/workspaces/{WORKSPACE_ID}/channels/{CHANNEL_ID}/memberships/me"),
        None,
        ChannelMembershipNotFound(),
        404,
        "channel_membership_not_found",
        "The channel membership was not found.",
    ),
    ChannelErrorCase(
        "add_membership",
        "PUT",
        (f"/api/v1/workspaces/{WORKSPACE_ID}/channels/{CHANNEL_ID}/memberships/{TARGET_USER_ID}"),
        None,
        TargetWorkspaceMembershipNotFound(),
        404,
        "target_workspace_membership_not_found",
        "The target user is not a member of the workspace.",
    ),
    ChannelErrorCase(
        "join_channel",
        "PUT",
        (f"/api/v1/workspaces/{WORKSPACE_ID}/channels/{CHANNEL_ID}/memberships/me"),
        None,
        ChannelArchived(),
        409,
        "channel_archived",
        "The channel is archived.",
    ),
    ChannelErrorCase(
        "get_channel",
        "GET",
        f"/api/v1/workspaces/{WORKSPACE_ID}/channels/{CHANNEL_ID}",
        None,
        UnmappedChannelError(),
        400,
        "channel_error",
        "The channel operation failed.",
    ),
]


@pytest.mark.parametrize("case", ERROR_CASES, ids=lambda case: case.code)
async def test_channel_errors_use_stable_problem_details(
    channel_api: ChannelApiHarness,
    case: ChannelErrorCase,
) -> None:
    channel_api.channel_service.errors[case.operation] = case.error
    request_kwargs: dict[str, object] = {"headers": channel_api.mutation_headers()}
    if case.payload is not None:
        request_kwargs["json"] = case.payload

    response = await channel_api.client.request(case.method, case.path, **request_kwargs)

    assert response.status_code == case.status
    assert response.headers["content-type"].startswith("application/problem+json")
    assert response.json() == {
        "type": "about:blank",
        "title": HTTPStatus(case.status).phrase,
        "status": case.status,
        "detail": case.detail,
        "code": case.code,
        "request_id": response.headers["x-request-id"],
    }
    assert_no_store(response)


@dataclass(frozen=True)
class RouteCase:
    method: str
    path: str
    payload: dict[str, str] | None = None


ALL_ROUTE_CASES = [
    RouteCase("POST", f"/api/v1/workspaces/{WORKSPACE_ID}/channels", {"name": "general"}),
    RouteCase("GET", f"/api/v1/workspaces/{WORKSPACE_ID}/channels"),
    RouteCase("GET", f"/api/v1/workspaces/{WORKSPACE_ID}/channels/{CHANNEL_ID}"),
    RouteCase(
        "PATCH",
        f"/api/v1/workspaces/{WORKSPACE_ID}/channels/{CHANNEL_ID}",
        {"visibility": "private"},
    ),
    RouteCase("POST", f"/api/v1/workspaces/{WORKSPACE_ID}/channels/{CHANNEL_ID}/archive"),
    RouteCase("POST", f"/api/v1/workspaces/{WORKSPACE_ID}/channels/{CHANNEL_ID}/unarchive"),
    RouteCase(
        "GET",
        f"/api/v1/workspaces/{WORKSPACE_ID}/channels/{CHANNEL_ID}/memberships",
    ),
    RouteCase(
        "GET",
        f"/api/v1/workspaces/{WORKSPACE_ID}/channels/{CHANNEL_ID}/memberships/me",
    ),
    RouteCase(
        "PUT",
        f"/api/v1/workspaces/{WORKSPACE_ID}/channels/{CHANNEL_ID}/memberships/me",
    ),
    RouteCase(
        "DELETE",
        f"/api/v1/workspaces/{WORKSPACE_ID}/channels/{CHANNEL_ID}/memberships/me",
    ),
    RouteCase(
        "PUT",
        (f"/api/v1/workspaces/{WORKSPACE_ID}/channels/{CHANNEL_ID}/memberships/{TARGET_USER_ID}"),
    ),
    RouteCase(
        "DELETE",
        (f"/api/v1/workspaces/{WORKSPACE_ID}/channels/{CHANNEL_ID}/memberships/{TARGET_USER_ID}"),
    ),
]


@pytest.mark.parametrize(
    "case",
    ALL_ROUTE_CASES,
    ids=lambda case: f"{case.method}-{case.path.rsplit('/', maxsplit=1)[-1]}",
)
async def test_every_channel_route_requires_an_authenticated_access_session(
    channel_api: ChannelApiHarness,
    case: RouteCase,
) -> None:
    channel_api.client.cookies.clear()
    request_kwargs: dict[str, object] = {"headers": channel_api.mutation_headers()}
    if case.payload is not None:
        request_kwargs["json"] = case.payload

    response = await channel_api.client.request(case.method, case.path, **request_kwargs)

    assert response.status_code == 401
    assert response.json()["code"] == "authentication_required"
    assert response.headers["content-type"].startswith("application/problem+json")
    assert_no_store(response)
    assert channel_api.identity_service.authenticate_calls == [None]
    assert channel_api.channel_service.calls == {}


MUTATION_ROUTE_CASES = [
    case for case in ALL_ROUTE_CASES if case.method in {"POST", "PATCH", "PUT", "DELETE"}
]


@pytest.mark.parametrize(
    "case",
    MUTATION_ROUTE_CASES,
    ids=lambda case: f"{case.method}-{case.path.rsplit('/', maxsplit=1)[-1]}",
)
async def test_every_channel_mutation_requires_the_exact_configured_origin(
    channel_api: ChannelApiHarness,
    case: RouteCase,
) -> None:
    headers = {"X-CSRF-Token": PRIMARY_CSRF_TOKEN}
    request_kwargs: dict[str, object] = {"headers": headers}
    if case.payload is not None:
        request_kwargs["json"] = case.payload

    response = await channel_api.client.request(case.method, case.path, **request_kwargs)

    assert response.status_code == 403
    assert response.json()["code"] == "origin_not_allowed"
    assert_no_store(response)
    assert channel_api.identity_service.csrf_calls == []
    assert channel_api.channel_service.calls == {}


@pytest.mark.parametrize(
    "origin",
    ["http://test/", "https://test", "HTTP://TEST"],
    ids=["trailing-slash", "wrong-scheme", "different-case"],
)
async def test_channel_mutation_rejects_inexact_origins(
    channel_api: ChannelApiHarness,
    origin: str,
) -> None:
    response = await channel_api.client.post(
        f"/api/v1/workspaces/{WORKSPACE_ID}/channels",
        headers={"Origin": origin, "X-CSRF-Token": PRIMARY_CSRF_TOKEN},
        json={"name": "general"},
    )

    assert response.status_code == 403
    assert response.json()["code"] == "origin_not_allowed"
    assert_no_store(response)
    assert channel_api.identity_service.csrf_calls == []
    assert channel_api.channel_service.calls == {}


@pytest.mark.parametrize(
    ("cookie_token", "header_token"),
    [
        (None, PRIMARY_CSRF_TOKEN),
        (PRIMARY_CSRF_TOKEN, None),
        (PRIMARY_CSRF_TOKEN, "different"),
        ("different", PRIMARY_CSRF_TOKEN),
    ],
)
async def test_channel_mutation_requires_double_submit_csrf_equality(
    channel_api: ChannelApiHarness,
    cookie_token: str | None,
    header_token: str | None,
) -> None:
    if cookie_token is None:
        channel_api.client.cookies.delete("klack_csrf")
    else:
        channel_api.client.cookies.set("klack_csrf", cookie_token)
    headers = dict(ORIGIN_HEADERS)
    if header_token is not None:
        headers["X-CSRF-Token"] = header_token

    response = await channel_api.client.post(
        f"/api/v1/workspaces/{WORKSPACE_ID}/channels",
        headers=headers,
        json={"name": "general"},
    )

    assert response.status_code == 403
    assert response.json()["code"] == "csrf_validation_failed"
    assert_no_store(response)
    assert channel_api.identity_service.csrf_calls == []
    assert channel_api.channel_service.calls == {}


async def test_channel_mutation_binds_matching_csrf_to_the_current_session(
    channel_api: ChannelApiHarness,
) -> None:
    client = channel_api.client
    client.cookies.set("klack_access", SECOND_ACCESS_TOKEN)
    client.cookies.set("klack_csrf", PRIMARY_CSRF_TOKEN)
    path = f"/api/v1/workspaces/{WORKSPACE_ID}/channels"

    wrong_session = await client.post(
        path,
        headers=channel_api.mutation_headers(PRIMARY_CSRF_TOKEN),
        json={"name": "general"},
    )

    assert wrong_session.status_code == 403
    assert wrong_session.json()["code"] == "csrf_validation_failed"
    assert_no_store(wrong_session)
    assert channel_api.identity_service.csrf_calls[0] == {
        "identity": channel_api.identity_service.second_identity,
        "csrf_token": PRIMARY_CSRF_TOKEN,
    }
    assert channel_api.channel_service.calls == {}

    client.cookies.set("klack_csrf", SECOND_CSRF_TOKEN)
    correct_session = await client.post(
        path,
        headers=channel_api.mutation_headers(SECOND_CSRF_TOKEN),
        json={"name": "general"},
    )

    assert correct_session.status_code == 201
    assert_no_store(correct_session)
    assert channel_api.channel_service.calls["create_channel"] == [
        {
            "actor_user_id": ACTOR_USER_ID,
            "workspace_id": WORKSPACE_ID,
            "name": "general",
            "visibility": ChannelVisibility.PUBLIC,
        },
    ]
