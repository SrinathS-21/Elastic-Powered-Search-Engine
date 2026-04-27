"""FastAPI service entrypoint.

Production is intentionally scoped to the PBR UI + its supporting `/ui-api/*`
endpoints. Other endpoints (search/autocomplete/quality/docs) are excluded to
minimize the public surface area.
"""

from __future__ import annotations

import asyncio

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from starlette.responses import Response

from src.core.clients import (
    active_search_backend,
    first_available_backend,
    is_backend_reachable,
    normalize_search_backend,
    reset_request_search_backend,
    set_request_search_backend,
)
from src.core.config import CORS_ALLOW_ORIGINS, UI_DIR
from src.core.lifecycle import lifespan
from src.routers import ui as ui_router


app = FastAPI(
    title="Pepagora PBR API",
    version="1.0.0",
    lifespan=lifespan,
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ALLOW_ORIGINS,
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)


_NON_API_PATH_PREFIXES = ("/ui/", "/docs", "/redoc", "/openapi")

@app.middleware("http")
async def search_backend_override_middleware(request: Request, call_next):
    path = request.url.path

    # Skip backend probing entirely for static/doc/root HTML requests — they don't need it
    # and the network ping adds 300ms–5s for zero benefit.
    is_non_api = (
        path in ("/", "/pbr-quick-post-enrich.html")
        or any(path.startswith(p) for p in _NON_API_PATH_PREFIXES)
    )

    requested_backend_raw = (
        request.query_params.get("backend")
        or request.headers.get("x-search-backend")
        or ""
    ).strip()
    requested_backend = normalize_search_backend(requested_backend_raw) if requested_backend_raw else None

    selected_backend = requested_backend
    fallback_from: str | None = None

    if not is_non_api:
        if requested_backend:
            if not is_backend_reachable(requested_backend):
                alternate = first_available_backend(exclude_backend=requested_backend)
                if alternate:
                    selected_backend = alternate
                    fallback_from = requested_backend
        else:
            selected_backend = first_available_backend(preferred_backend=active_search_backend())

    token = set_request_search_backend(selected_backend) if selected_backend else None
    try:
        try:
            response = await call_next(request)
        except asyncio.CancelledError:
            return Response(status_code=204)
        resolved_backend = active_search_backend()
        response.headers["x-search-backend"] = resolved_backend
        if requested_backend:
            response.headers["x-search-backend-requested"] = requested_backend
            response.headers["x-search-backend-available"] = "true" if is_backend_reachable(requested_backend) else "false"
        if fallback_from and selected_backend:
            response.headers["x-search-backend-fallback-from"] = fallback_from
            response.headers["x-search-backend-fallback-to"] = selected_backend
        return response
    finally:
        if token is not None:
            reset_request_search_backend(token)

if UI_DIR.exists():
    app.mount("/ui", StaticFiles(directory=UI_DIR), name="ui")

app.include_router(ui_router.router)
