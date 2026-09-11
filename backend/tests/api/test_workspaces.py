"""HTTP contracts for workspaces, memberships, and manual invitation links."""

from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from http import HTTPStatus
from uuid import UUID

import pytest
from fastapi import FastAPI
from httpx import AsyncClient, Response

from klack.modules.identity.api.dependencies import get_identity_service
from klack.modules.identity.domain.entities import AuthenticatedIdentity, AuthSession, User
from klack.modules.identity.domain.errors import AuthenticationRequired, CsrfValidationFailed
from klack.modules.workspaces.api.dependencies import get_workspace_service
from klack.modules.workspaces.application.service import InvitationCreation
from klack.modules.workspaces.domain.entities import (
    Workspace,
    WorkspaceInvitation,
    WorkspaceMembership,
    WorkspaceRole,
)
from klack.modules.workspaces.domain.errors import (
    InvalidInvitationToken,
    InvalidWorkspaceName,
    InvitationNotActive,
    InvitationNotFound,
    MembershipAlreadyExists,
    MembershipNotFound,
    OwnerInvariantViolation,
    WorkspaceNotFound,
    WorkspacePermissionDenied,
)

NOW = datetime(2026, 9, 10, 12, 30, tzinfo=UTC)
ACTOR_USER_ID = UUID("11111111-1111-4111-8111-111111111111")
TARGET_USER_ID = UUID("22222222-2222-4222-8222-222222222222")
WORKSPACE_ID = UUID("33333333-3333-4333-8333-333333333333")
SECOND_WORKSPACE_ID = UUID("44444444-4444-4444-8444-444444444444")
INVITATION_ID = UUID("55555555-5555-4555-8555-555555555555")
ROTATED_INVITATION_ID = UUID("66666666-6666-4666-8666-666666666666")
PRIMARY_SESSION_ID = UUID("77777777-7777-4777-8777-777777777777")
SECOND_SESSION_ID = UUID("88888888-8888-4888-8888-888888888888")
PRIMARY_ACCESS_TOKEN = "primary-access"
SECOND_ACCESS_TOKEN = "second-access"
PRIMARY_CSRF_TOKEN = "primary-session-csrf"
SECOND_CSRF_TOKEN = "second-session-csrf"
RAW_INVITATION_TOKEN = f"{INVITATION_ID}.manual-secret_ABC"
ROTATED_RAW_INVITATION_TOKEN = f"{ROTATED_INVITATION_ID}.replacement-secret_XYZ"
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
        user_agent="Workspace API test",
    )


def make_workspace(
    workspace_id: UUID = WORKSPACE_ID,
    *,
    name: str = "Product",
) -> Workspace:
    return Workspace(
        id=workspace_id,
        name=name,
        created_by_user_id=ACTOR_USER_ID,
        created_at=NOW,
        updated_at=NOW + timedelta(minutes=5),
    )


def make_membership(
    user_id: UUID = ACTOR_USER_ID,
    *,
    role: WorkspaceRole = WorkspaceRole.OWNER,
) -> WorkspaceMembership:
    return WorkspaceMembership(
        workspace_id=WORKSPACE_ID,
        user_id=user_id,
        role=role,
        joined_at=NOW + timedelta(minutes=1),
    )


def make_invitation(
    invitation_id: UUID = INVITATION_ID,
    *,
    token_hash: str = "server-only-token-digest",
) -> WorkspaceInvitation:
    return WorkspaceInvitation(
        id=invitation_id,
        workspace_id=WORKSPACE_ID,
        created_by_user_id=ACTOR_USER_ID,
        token_hash=token_hash,
        created_at=NOW + timedelta(minutes=2),
        expires_at=NOW + timedelta(days=7),
        accepted_at=None,
        accepted_by_user_id=None,
        revoked_at=None,
        revoked_by_user_id=None,
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


class FakeWorkspaceService:
    """Record API-to-application calls and return deterministic domain values."""

    def __init__(self) -> None:
        self.workspace = make_workspace()
        self.renamed_workspace = make_workspace(name="Platform")
        self.workspaces = [self.workspace, make_workspace(SECOND_WORKSPACE_ID, name="Support")]
        self.owner_membership = make_membership()
        self.member_membership = make_membership(
            TARGET_USER_ID,
            role=WorkspaceRole.MEMBER,
        )
        self.changed_membership = make_membership(
            TARGET_USER_ID,
            role=WorkspaceRole.ADMIN,
        )
        self.invitation = make_invitation()
        self.rotated_invitation = make_invitation(
            ROTATED_INVITATION_ID,
            token_hash="replacement-server-only-digest",
        )
        self.calls: defaultdict[str, list[dict[str, object]]] = defaultdict(list)
        self.errors: dict[str, Exception] = {}

    def _record(self, operation: str, **kwargs: object) -> None:
        self.calls[operation].append(kwargs)
        error = self.errors.get(operation)
        if error is not None:
            raise error

    async def create_workspace(self, **kwargs: object) -> Workspace:
        self._record("create_workspace", **kwargs)
        return self.workspace

    async def list_workspaces(self, **kwargs: object) -> list[Workspace]:
        self._record("list_workspaces", **kwargs)
        return self.workspaces

    async def get_workspace(self, **kwargs: object) -> Workspace:
        self._record("get_workspace", **kwargs)
        return self.workspace

    async def rename_workspace(self, **kwargs: object) -> Workspace:
        self._record("rename_workspace", **kwargs)
        return self.renamed_workspace

    async def list_memberships(self, **kwargs: object) -> list[WorkspaceMembership]:
        self._record("list_memberships", **kwargs)
        return [self.owner_membership, self.member_membership]

    async def get_current_membership(self, **kwargs: object) -> WorkspaceMembership:
        self._record("get_current_membership", **kwargs)
        return self.owner_membership

    async def change_membership_role(self, **kwargs: object) -> WorkspaceMembership:
        self._record("change_membership_role", **kwargs)
        return self.changed_membership

    async def remove_membership(self, **kwargs: object) -> None:
        self._record("remove_membership", **kwargs)

    async def leave_workspace(self, **kwargs: object) -> None:
        self._record("leave_workspace", **kwargs)

    async def create_invitation(self, **kwargs: object) -> InvitationCreation:
        self._record("create_invitation", **kwargs)
        return InvitationCreation(
            invitation=self.invitation,
            raw_token=RAW_INVITATION_TOKEN,
        )

    async def list_invitations(self, **kwargs: object) -> list[WorkspaceInvitation]:
        self._record("list_invitations", **kwargs)
        return [self.invitation]

    async def rotate_invitation(self, **kwargs: object) -> InvitationCreation:
        self._record("rotate_invitation", **kwargs)
        return InvitationCreation(
            invitation=self.rotated_invitation,
            raw_token=ROTATED_RAW_INVITATION_TOKEN,
        )

    async def revoke_invitation(self, **kwargs: object) -> None:
        self._record("revoke_invitation", **kwargs)

    async def accept_invitation(self, **kwargs: object) -> WorkspaceMembership:
        self._record("accept_invitation", **kwargs)
        return self.member_membership


@dataclass(frozen=True)
class WorkspaceApiHarness:
    client: AsyncClient
    identity_service: FakeIdentityService
    workspace_service: FakeWorkspaceService

    def mutation_headers(self, csrf_token: str = PRIMARY_CSRF_TOKEN) -> dict[str, str]:
        return {**ORIGIN_HEADERS, "X-CSRF-Token": csrf_token}


@pytest.fixture
def workspace_api(app: FastAPI, client: AsyncClient) -> WorkspaceApiHarness:
    identity_service = FakeIdentityService()
    workspace_service = FakeWorkspaceService()
    app.dependency_overrides[get_identity_service] = lambda: identity_service
    app.dependency_overrides[get_workspace_service] = lambda: workspace_service
    client.cookies.set("klack_access", PRIMARY_ACCESS_TOKEN)
    client.cookies.set("klack_csrf", PRIMARY_CSRF_TOKEN)
    return WorkspaceApiHarness(
        client=client,
        identity_service=identity_service,
        workspace_service=workspace_service,
    )


def expected_workspace(workspace: Workspace) -> dict[str, str]:
    return {
        "id": str(workspace.id),
        "name": workspace.name,
        "created_by_user_id": str(workspace.created_by_user_id),
        "created_at": timestamp(workspace.created_at),
        "updated_at": timestamp(workspace.updated_at),
    }


def expected_membership(membership: WorkspaceMembership) -> dict[str, str]:
    return {
        "workspace_id": str(membership.workspace_id),
        "user_id": str(membership.user_id),
        "role": membership.role.value,
        "joined_at": timestamp(membership.joined_at),
    }


def expected_invitation(invitation: WorkspaceInvitation) -> dict[str, str | None]:
    return {
        "id": str(invitation.id),
        "workspace_id": str(invitation.workspace_id),
        "created_by_user_id": str(invitation.created_by_user_id),
        "created_at": timestamp(invitation.created_at),
        "expires_at": timestamp(invitation.expires_at),
        "accepted_at": None,
        "accepted_by_user_id": None,
        "revoked_at": None,
        "revoked_by_user_id": None,
    }


def assert_no_store(response: Response) -> None:
    assert response.headers["cache-control"] == "no-store"


async def test_workspace_routes_expose_stable_shapes_and_actor_calls(
    workspace_api: WorkspaceApiHarness,
) -> None:
    service = workspace_api.workspace_service
    client = workspace_api.client

    created = await client.post(
        "/api/v1/workspaces",
        headers=workspace_api.mutation_headers(),
        json={"name": "Product"},
    )
    listed = await client.get("/api/v1/workspaces")
    loaded = await client.get(f"/api/v1/workspaces/{WORKSPACE_ID}")
    renamed = await client.patch(
        f"/api/v1/workspaces/{WORKSPACE_ID}",
        headers=workspace_api.mutation_headers(),
        json={"name": "Platform"},
    )

    assert created.status_code == 201
    assert created.json() == expected_workspace(service.workspace)
    assert listed.status_code == 200
    assert listed.json() == {
        "workspaces": [expected_workspace(item) for item in service.workspaces],
    }
    assert loaded.status_code == 200
    assert loaded.json() == expected_workspace(service.workspace)
    assert renamed.status_code == 200
    assert renamed.json() == expected_workspace(service.renamed_workspace)
    for response in (created, listed, loaded, renamed):
        assert_no_store(response)

    assert service.calls["create_workspace"] == [
        {"actor_user_id": ACTOR_USER_ID, "name": "Product"},
    ]
    assert service.calls["list_workspaces"] == [{"actor_user_id": ACTOR_USER_ID}]
    assert service.calls["get_workspace"] == [
        {"actor_user_id": ACTOR_USER_ID, "workspace_id": WORKSPACE_ID},
    ]
    assert service.calls["rename_workspace"] == [
        {
            "actor_user_id": ACTOR_USER_ID,
            "workspace_id": WORKSPACE_ID,
            "name": "Platform",
        },
    ]


async def test_workspace_names_are_trimmed_before_request_length_validation(
    workspace_api: WorkspaceApiHarness,
) -> None:
    raw_name = f"  {'x' * 100}  "

    created = await workspace_api.client.post(
        "/api/v1/workspaces",
        headers=workspace_api.mutation_headers(),
        json={"name": raw_name},
    )
    renamed = await workspace_api.client.patch(
        f"/api/v1/workspaces/{WORKSPACE_ID}",
        headers=workspace_api.mutation_headers(),
        json={"name": raw_name},
    )

    assert created.status_code == 201
    assert renamed.status_code == 200
    assert workspace_api.workspace_service.calls["create_workspace"] == [
        {"actor_user_id": ACTOR_USER_ID, "name": "x" * 100},
    ]
    assert workspace_api.workspace_service.calls["rename_workspace"] == [
        {
            "actor_user_id": ACTOR_USER_ID,
            "workspace_id": WORKSPACE_ID,
            "name": "x" * 100,
        },
    ]


async def test_membership_routes_expose_stable_shapes_and_actor_calls(
    workspace_api: WorkspaceApiHarness,
) -> None:
    service = workspace_api.workspace_service
    client = workspace_api.client
    base_path = f"/api/v1/workspaces/{WORKSPACE_ID}/memberships"

    listed = await client.get(base_path)
    current = await client.get(f"{base_path}/me")
    changed = await client.patch(
        f"{base_path}/{TARGET_USER_ID}",
        headers=workspace_api.mutation_headers(),
        json={"role": "admin"},
    )
    removed = await client.delete(
        f"{base_path}/{TARGET_USER_ID}",
        headers=workspace_api.mutation_headers(),
    )
    left = await client.post(
        f"/api/v1/workspaces/{WORKSPACE_ID}/leave",
        headers=workspace_api.mutation_headers(),
    )

    assert listed.status_code == 200
    assert listed.json() == {
        "memberships": [
            expected_membership(service.owner_membership),
            expected_membership(service.member_membership),
        ],
    }
    assert current.status_code == 200
    assert current.json() == expected_membership(service.owner_membership)
    assert changed.status_code == 200
    assert changed.json() == expected_membership(service.changed_membership)
    for response in (removed, left):
        assert response.status_code == 204
        assert response.content == b""
    for response in (listed, current, changed, removed, left):
        assert_no_store(response)

    common = {"actor_user_id": ACTOR_USER_ID, "workspace_id": WORKSPACE_ID}
    assert service.calls["list_memberships"] == [common]
    assert service.calls["get_current_membership"] == [common]
    assert service.calls["change_membership_role"] == [
        {**common, "target_user_id": TARGET_USER_ID, "role": WorkspaceRole.ADMIN},
    ]
    assert service.calls["remove_membership"] == [
        {**common, "target_user_id": TARGET_USER_ID},
    ]
    assert service.calls["leave_workspace"] == [common]


async def test_invitation_routes_reveal_only_creation_links_and_call_actor_service(
    workspace_api: WorkspaceApiHarness,
) -> None:
    service = workspace_api.workspace_service
    client = workspace_api.client
    base_path = f"/api/v1/workspaces/{WORKSPACE_ID}/invitations"

    created = await client.post(base_path, headers=workspace_api.mutation_headers())
    listed = await client.get(base_path)
    rotated = await client.post(
        f"{base_path}/{INVITATION_ID}/rotate",
        headers=workspace_api.mutation_headers(),
    )
    revoked = await client.delete(
        f"{base_path}/{INVITATION_ID}",
        headers=workspace_api.mutation_headers(),
    )
    accepted = await client.post(
        "/api/v1/workspace-invitations/accept",
        headers=workspace_api.mutation_headers(),
        json={"token": RAW_INVITATION_TOKEN},
    )

    assert created.status_code == 201
    assert created.json() == {
        "invitation": expected_invitation(service.invitation),
        "invite_url": f"http://test/join#token={RAW_INVITATION_TOKEN}",
    }
    assert listed.status_code == 200
    assert listed.json() == {"invitations": [expected_invitation(service.invitation)]}
    assert rotated.status_code == 201
    assert rotated.json() == {
        "invitation": expected_invitation(service.rotated_invitation),
        "invite_url": f"http://test/join#token={ROTATED_RAW_INVITATION_TOKEN}",
    }
    assert revoked.status_code == 204
    assert revoked.content == b""
    assert accepted.status_code == 201
    assert accepted.json() == expected_membership(service.member_membership)
    for response in (created, listed, rotated, revoked, accepted):
        assert_no_store(response)

    assert "invite_url" not in listed.text
    assert "token_hash" not in created.text
    assert "token_hash" not in listed.text
    assert "token_hash" not in rotated.text
    assert "server-only-token-digest" not in created.text
    assert "server-only-token-digest" not in listed.text
    assert "replacement-server-only-digest" not in rotated.text
    assert RAW_INVITATION_TOKEN in created.text
    assert RAW_INVITATION_TOKEN not in listed.text
    assert "?token=" not in created.json()["invite_url"]
    assert created.json()["invite_url"].count("#token=") == 1

    common = {"actor_user_id": ACTOR_USER_ID, "workspace_id": WORKSPACE_ID}
    assert service.calls["create_invitation"] == [common]
    assert service.calls["list_invitations"] == [common]
    assert service.calls["rotate_invitation"] == [
        {**common, "invitation_id": INVITATION_ID},
    ]
    assert service.calls["revoke_invitation"] == [
        {**common, "invitation_id": INVITATION_ID},
    ]
    assert service.calls["accept_invitation"] == [
        {"actor_user_id": ACTOR_USER_ID, "raw_token": RAW_INVITATION_TOKEN},
    ]


async def test_workspace_requests_are_strict_and_redact_rejected_values(
    workspace_api: WorkspaceApiHarness,
) -> None:
    client = workspace_api.client
    service = workspace_api.workspace_service

    invalid_name = await client.post(
        "/api/v1/workspaces",
        headers=workspace_api.mutation_headers(),
        json={"name": "", "unexpected": "private-workspace-value"},
    )
    invalid_role = await client.patch(
        f"/api/v1/workspaces/{WORKSPACE_ID}/memberships/{TARGET_USER_ID}",
        headers=workspace_api.mutation_headers(),
        json={"role": "superuser", "note": "private-role-value"},
    )
    rejected_token = await client.post(
        "/api/v1/workspace-invitations/accept",
        headers=workspace_api.mutation_headers(),
        json={"token": "highly-sensitive-invite", "unexpected": "private-token-value"},
    )

    for response in (invalid_name, invalid_role, rejected_token):
        assert response.status_code == 422
        assert response.headers["content-type"].startswith("application/problem+json")
        assert response.json()["code"] == "validation_error"
        assert response.json()["request_id"] == response.headers["x-request-id"]
        assert_no_store(response)
    assert set(invalid_name.json()["field_errors"]) == {"name", "unexpected"}
    assert set(invalid_role.json()["field_errors"]) == {"role", "note"}
    assert set(rejected_token.json()["field_errors"]) == {"unexpected"}
    assert "private-workspace-value" not in invalid_name.text
    assert "private-role-value" not in invalid_role.text
    assert "highly-sensitive-invite" not in rejected_token.text
    assert "private-token-value" not in rejected_token.text
    assert service.calls == {}


@dataclass(frozen=True)
class WorkspaceErrorCase:
    operation: str
    method: str
    path: str
    payload: dict[str, str] | None
    error: Exception
    status: int
    code: str
    detail: str


ERROR_CASES = [
    WorkspaceErrorCase(
        "create_workspace",
        "POST",
        "/api/v1/workspaces",
        {"name": "Product"},
        InvalidWorkspaceName(),
        422,
        "validation_error",
        "The workspace name is invalid.",
    ),
    WorkspaceErrorCase(
        "get_workspace",
        "GET",
        f"/api/v1/workspaces/{WORKSPACE_ID}",
        None,
        WorkspaceNotFound(),
        404,
        "workspace_not_found",
        "The workspace was not found.",
    ),
    WorkspaceErrorCase(
        "rename_workspace",
        "PATCH",
        f"/api/v1/workspaces/{WORKSPACE_ID}",
        {"name": "Platform"},
        WorkspacePermissionDenied(),
        403,
        "workspace_permission_denied",
        "The current membership cannot perform this operation.",
    ),
    WorkspaceErrorCase(
        "change_membership_role",
        "PATCH",
        f"/api/v1/workspaces/{WORKSPACE_ID}/memberships/{TARGET_USER_ID}",
        {"role": "admin"},
        MembershipNotFound(),
        404,
        "membership_not_found",
        "The membership was not found.",
    ),
    WorkspaceErrorCase(
        "accept_invitation",
        "POST",
        "/api/v1/workspace-invitations/accept",
        {"token": RAW_INVITATION_TOKEN},
        MembershipAlreadyExists(),
        409,
        "membership_already_exists",
        "The user already belongs to this workspace.",
    ),
    WorkspaceErrorCase(
        "leave_workspace",
        "POST",
        f"/api/v1/workspaces/{WORKSPACE_ID}/leave",
        None,
        OwnerInvariantViolation(),
        409,
        "owner_invariant_violation",
        "The workspace must retain at least one owner.",
    ),
    WorkspaceErrorCase(
        "revoke_invitation",
        "DELETE",
        f"/api/v1/workspaces/{WORKSPACE_ID}/invitations/{INVITATION_ID}",
        None,
        InvitationNotFound(),
        404,
        "invitation_not_found",
        "The invitation was not found.",
    ),
    WorkspaceErrorCase(
        "rotate_invitation",
        "POST",
        f"/api/v1/workspaces/{WORKSPACE_ID}/invitations/{INVITATION_ID}/rotate",
        None,
        InvitationNotActive(),
        409,
        "invitation_not_active",
        "The invitation is no longer active.",
    ),
    WorkspaceErrorCase(
        "accept_invitation",
        "POST",
        "/api/v1/workspace-invitations/accept",
        {"token": "sensitive-invalid-invitation"},
        InvalidInvitationToken(),
        400,
        "invalid_invitation_token",
        "The invitation is invalid or expired.",
    ),
]


@pytest.mark.parametrize("case", ERROR_CASES, ids=lambda case: case.code)
async def test_workspace_errors_use_stable_problem_details(
    workspace_api: WorkspaceApiHarness,
    case: WorkspaceErrorCase,
) -> None:
    workspace_api.workspace_service.errors[case.operation] = case.error
    request_kwargs: dict[str, object] = {"headers": workspace_api.mutation_headers()}
    if case.payload is not None:
        request_kwargs["json"] = case.payload

    response = await workspace_api.client.request(case.method, case.path, **request_kwargs)

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
    assert "server-only-token-digest" not in response.text
    assert "sensitive-invalid-invitation" not in response.text


async def test_workspace_routes_require_an_authenticated_access_session(
    workspace_api: WorkspaceApiHarness,
) -> None:
    workspace_api.client.cookies.clear()

    read_response = await workspace_api.client.get("/api/v1/workspaces")
    mutation_response = await workspace_api.client.post(
        "/api/v1/workspaces",
        headers=workspace_api.mutation_headers(),
        json={"name": "Product"},
    )

    for response in (read_response, mutation_response):
        assert response.status_code == 401
        assert response.json()["code"] == "authentication_required"
        assert response.headers["content-type"].startswith("application/problem+json")
        assert_no_store(response)
    assert workspace_api.identity_service.authenticate_calls == [None, None]
    assert workspace_api.workspace_service.calls == {}


@pytest.mark.parametrize(
    "origin",
    [None, "http://test/", "https://test", "HTTP://TEST"],
    ids=["missing", "trailing-slash", "wrong-scheme", "different-case"],
)
async def test_mutation_dependency_requires_the_exact_configured_origin(
    workspace_api: WorkspaceApiHarness,
    origin: str | None,
) -> None:
    headers = {"X-CSRF-Token": PRIMARY_CSRF_TOKEN}
    if origin is not None:
        headers["Origin"] = origin

    response = await workspace_api.client.post(
        "/api/v1/workspaces",
        headers=headers,
        json={"name": "Product"},
    )

    assert response.status_code == 403
    assert response.json()["code"] == "origin_not_allowed"
    assert_no_store(response)
    assert workspace_api.identity_service.csrf_calls == []
    assert workspace_api.workspace_service.calls == {}


@pytest.mark.parametrize(
    ("cookie_token", "header_token"),
    [
        (None, PRIMARY_CSRF_TOKEN),
        (PRIMARY_CSRF_TOKEN, None),
        (PRIMARY_CSRF_TOKEN, "different"),
        ("different", PRIMARY_CSRF_TOKEN),
    ],
)
async def test_mutation_dependency_requires_double_submit_csrf_equality(
    workspace_api: WorkspaceApiHarness,
    cookie_token: str | None,
    header_token: str | None,
) -> None:
    if cookie_token is None:
        workspace_api.client.cookies.delete("klack_csrf")
    else:
        workspace_api.client.cookies.set("klack_csrf", cookie_token)
    headers = dict(ORIGIN_HEADERS)
    if header_token is not None:
        headers["X-CSRF-Token"] = header_token

    response = await workspace_api.client.post(
        "/api/v1/workspaces",
        headers=headers,
        json={"name": "Product"},
    )

    assert response.status_code == 403
    assert response.json()["code"] == "csrf_validation_failed"
    assert_no_store(response)
    assert workspace_api.identity_service.csrf_calls == []
    assert workspace_api.workspace_service.calls == {}


async def test_mutation_dependency_binds_matching_csrf_to_the_current_session(
    workspace_api: WorkspaceApiHarness,
) -> None:
    client = workspace_api.client
    client.cookies.set("klack_access", SECOND_ACCESS_TOKEN)
    client.cookies.set("klack_csrf", PRIMARY_CSRF_TOKEN)

    wrong_session = await client.post(
        "/api/v1/workspaces",
        headers=workspace_api.mutation_headers(PRIMARY_CSRF_TOKEN),
        json={"name": "Product"},
    )

    assert wrong_session.status_code == 403
    assert wrong_session.json()["code"] == "csrf_validation_failed"
    assert workspace_api.identity_service.csrf_calls[0] == {
        "identity": workspace_api.identity_service.second_identity,
        "csrf_token": PRIMARY_CSRF_TOKEN,
    }
    assert workspace_api.workspace_service.calls == {}

    client.cookies.set("klack_csrf", SECOND_CSRF_TOKEN)
    correct_session = await client.post(
        "/api/v1/workspaces",
        headers=workspace_api.mutation_headers(SECOND_CSRF_TOKEN),
        json={"name": "Product"},
    )

    assert correct_session.status_code == 201
    assert workspace_api.workspace_service.calls["create_workspace"] == [
        {"actor_user_id": ACTOR_USER_ID, "name": "Product"},
    ]
