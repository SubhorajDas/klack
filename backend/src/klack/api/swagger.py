"""Development-only interactive API documentation helpers."""

import json

from fastapi.openapi.docs import get_swagger_ui_html
from starlette.responses import HTMLResponse

from klack.modules.identity.api.browser_security import CSRF_COOKIE, CSRF_HEADER


def development_swagger_ui_html(
    *,
    openapi_url: str,
    api_prefix: str,
    title: str,
) -> HTMLResponse:
    """Render Swagger UI with narrowly scoped double-submit CSRF presentation."""
    response = get_swagger_ui_html(
        openapi_url=openapi_url,
        title=title,
        swagger_ui_parameters={
            "showMutatedRequest": False,
            "validatorUrl": None,
        },
    )
    marker = "const ui = SwaggerUIBundle({"
    script = f"""
    requestInterceptor: (request) => {{
        const mutationMethods = new Set(["POST", "PUT", "PATCH", "DELETE"]);
        const method = (request.method || "").toUpperCase();
        let target;
        try {{
            target = new URL(request.url, window.location.href);
        }} catch (_error) {{
            return request;
        }}
        const apiPrefix = {json.dumps(api_prefix)};
        const targetsApi = target.pathname === apiPrefix ||
            target.pathname.startsWith(`${{apiPrefix}}/`);
        if (!mutationMethods.has(method) ||
            target.origin !== window.location.origin || !targetsApi) {{
            return request;
        }}
        const cookieName = {json.dumps(CSRF_COOKIE)};
        const cookie = document.cookie
            .split(";")
            .map((part) => part.trim())
            .find((part) => part.startsWith(`${{cookieName}}=`));
        if (!cookie) {{
            return request;
        }}
        const encodedValue = cookie.slice(cookieName.length + 1);
        try {{
            request.headers = request.headers || {{}};
            request.headers[{json.dumps(CSRF_HEADER)}] = decodeURIComponent(encodedValue);
        }} catch (_error) {{
            return request;
        }}
        return request;
    }},
"""
    body = bytes(response.body).decode("utf-8").replace(marker, f"{marker}{script}", 1)
    return HTMLResponse(
        content=body,
        status_code=response.status_code,
        headers={"Cache-Control": "no-store"},
    )
