"""Development Swagger UI security and usability contracts."""

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from klack.bootstrap import create_app
from klack.core.config import AppEnvironment, Settings
from klack.core.db.session import DatabaseHealthCheck


async def _get(app: FastAPI, path: str):
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        return await client.get(path)


async def test_development_swagger_installs_same_origin_csrf_interceptor(
    settings: Settings,
    healthy_database_check: DatabaseHealthCheck,
) -> None:
    development_settings = settings.model_copy(
        update={"app_env": AppEnvironment.DEVELOPMENT},
    )
    app = create_app(
        development_settings,
        database_health_check=healthy_database_check,
    )

    response = await _get(app, "/docs")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert response.headers["cache-control"] == "no-store"
    assert "requestInterceptor" in response.text
    assert 'new Set(["POST", "PUT", "PATCH", "DELETE"])' in response.text
    assert "target.origin !== window.location.origin" in response.text
    assert 'const apiPrefix = "/api/v1"' in response.text
    assert 'const cookieName = "klack_csrf"' in response.text
    assert 'request.headers["X-CSRF-Token"]' in response.text
    assert development_settings.auth_jwt_secret_value() not in response.text
    assert development_settings.auth_refresh_secret_value() not in response.text
    assert development_settings.auth_action_secret_value() not in response.text
    assert development_settings.workspace_invitation_secret_value() not in response.text
    assert (await _get(app, "/openapi.json")).status_code == 200


@pytest.mark.parametrize(
    "environment",
    [AppEnvironment.TEST, AppEnvironment.STAGING, AppEnvironment.PRODUCTION],
)
async def test_interactive_documentation_is_absent_outside_development(
    settings: Settings,
    healthy_database_check: DatabaseHealthCheck,
    environment: AppEnvironment,
) -> None:
    app = create_app(
        settings.model_copy(update={"app_env": environment}),
        database_health_check=healthy_database_check,
    )

    assert (await _get(app, "/docs")).status_code == 404
    assert (await _get(app, "/redoc")).status_code == 404
    assert (await _get(app, "/docs/oauth2-redirect")).status_code == 404
    assert (await _get(app, "/openapi.json")).status_code == 200
