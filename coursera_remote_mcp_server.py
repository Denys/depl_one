#!/usr/bin/env python3
"""
Coursera Remote MCP Server for Claude.ai Custom Connectors
===========================================================

Implements the MCP "Streamable HTTP" transport so it can be registered
as a Remote MCP server in Claude.ai → Customize → Connectors → + (Add custom connector).

Auth model
----------
  - Public tools (catalog search, course details) work without any auth.
  - Enrolled-content tools (get_enrolled_courses, get_course_materials) require
    a Coursera OAuth 2.0 access token delivered as a Bearer token in the
    Authorization request header.
  - Claude.ai's connector "Advanced settings" supports OAuth 2.0 configuration
    so the token can be injected automatically on every request.

Requirements
------------
    pip install fastapi "uvicorn[standard]" httpx python-dotenv

Quick start (local testing)
---------------------------
    cp .env.example .env          # fill in COURSERA_CLIENT_ID / SECRET
    uvicorn coursera_remote_mcp_server:app --port 8000 --reload
    # Point Claude.ai connector at: http://localhost:8000/mcp
    # (use ngrok/tunnelmole for a public HTTPS URL during development)

Production deployment
---------------------
    Deploy to Railway, Fly.io, Render, or any host that gives you an HTTPS URL.
    Paste that URL into the Claude.ai "Add custom connector" → Remote MCP server URL.

What Coursera's API actually exposes via OAuth
----------------------------------------------
  ✓ Enrolled courses list   (limited API)
  ✓ Catalog search / course metadata   (public API, no auth needed)
  ✗ Video streams           (no public API; CDN-protected)
  ✗ Reading PDFs / HTML     (no public API)
  ✗ Quiz/assignment content (no public API)
  ✗ Discussion forums       (no public API for third parties)

  Summary: OAuth gives you enrollment metadata and public catalog data.
  Raw course content (videos, readings) is NOT accessible via Coursera's
  official API, regardless of Coursera Plus subscription level.
  Attempting to scrape it would violate Coursera's Terms of Service.

Coursera OAuth 2.0 setup
-------------------------
  1. Apply for Coursera's partner / developer program at
     https://partner.coursera.help/hc/en-us/articles/209819543
  2. Once approved, register an OAuth app to get CLIENT_ID and CLIENT_SECRET.
  3. Authorization endpoint: https://accounts.coursera.org/oauth2/v1/auth
  4. Token endpoint:         https://accounts.coursera.org/oauth2/v1/token
  5. Scopes: view_profile (and others granted by Coursera)
  6. Set COURSERA_CLIENT_ID, COURSERA_CLIENT_SECRET in your .env file.

  For local testing without approved credentials, the public tools (search /
  get_course / search_specializations) work with no credentials at all.
"""

from __future__ import annotations

import json
import logging
import os
import traceback
from typing import Any, AsyncIterator, Dict, Optional

import httpx
from dotenv import load_dotenv
from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse

load_dotenv()

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("coursera-remote-mcp")

# ──────────────────────────────────────────────
# Config
# ──────────────────────────────────────────────
SERVER_NAME = "coursera-remote-connector"
SERVER_VERSION = "0.2.0"
COURSERA_API_BASE = "https://api.coursera.org/api"

# OAuth app credentials (needed only for enrolled-content tools)
COURSERA_CLIENT_ID = os.getenv("COURSERA_CLIENT_ID", "")
COURSERA_CLIENT_SECRET = os.getenv("COURSERA_CLIENT_SECRET", "")

# ──────────────────────────────────────────────
# FastAPI application
# ──────────────────────────────────────────────
app = FastAPI(title="Coursera Remote MCP Connector", version=SERVER_VERSION)

# Claude.ai sends requests from its own domain; allow the origin.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["https://claude.ai", "http://localhost:*", "*"],
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["*"],
)


# ──────────────────────────────────────────────
# Coursera API helpers
# ──────────────────────────────────────────────

async def _api_get(
    client: httpx.AsyncClient,
    url: str,
    token: Optional[str] = None,
    timeout: int = 20,
) -> Dict[str, Any]:
    """Perform an authenticated or unauthenticated GET against the Coursera API."""
    headers: Dict[str, str] = {
        "Accept": "application/json",
        "User-Agent": f"{SERVER_NAME}/{SERVER_VERSION}",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    resp = await client.get(url, headers=headers, timeout=timeout)
    resp.raise_for_status()
    return resp.json()


async def tool_search_courses(args: Dict[str, Any], token: Optional[str]) -> Dict[str, Any]:
    """Search public Coursera catalog — no auth required."""
    query = str(args.get("query", "")).strip()
    limit = max(1, min(int(args.get("limit", 5)), 20))
    params = {
        "q": "search",
        "query": query,
        "limit": limit,
        "fields": "name,slug,photoUrl,partnerIds,primaryLanguages,certificates,workload,domainTypes",
    }
    url = httpx.URL(f"{COURSERA_API_BASE}/courses.v1", params=params)
    async with httpx.AsyncClient() as client:
        data = await _api_get(client, str(url))
    elements = data.get("elements", [])
    return {
        "query": query,
        "count": len(elements),
        "courses": [
            {
                "id": c.get("id"),
                "name": c.get("name"),
                "slug": c.get("slug"),
                "languages": c.get("primaryLanguages"),
                "workload": c.get("workload"),
                "domainTypes": c.get("domainTypes"),
            }
            for c in elements
        ],
    }


async def tool_get_course(args: Dict[str, Any], token: Optional[str]) -> Dict[str, Any]:
    """Get detailed course info by slug or numeric id — no auth required."""
    course_ref = str(args.get("course", "")).strip()
    if "/" in course_ref:
        course_ref = course_ref.rsplit("/", 1)[-1]

    params = {
        "q": "slug",
        "slug": course_ref,
        "fields": "name,slug,description,primaryLanguages,certificates,workload,photoUrl,domainTypes,skills,level,partnerIds",
    }
    url = httpx.URL(f"{COURSERA_API_BASE}/courses.v1", params=params)
    async with httpx.AsyncClient() as client:
        data = await _api_get(client, str(url))

    elements = data.get("elements", [])
    if not elements:
        return {"error": f"No course found for '{course_ref}'"}
    return elements[0]


async def tool_search_specializations(args: Dict[str, Any], token: Optional[str]) -> Dict[str, Any]:
    """Search public specializations catalog — no auth required."""
    query = str(args.get("query", "")).strip()
    limit = max(1, min(int(args.get("limit", 5)), 20))
    params = {
        "q": "search",
        "query": query,
        "limit": limit,
        "fields": "name,slug,description,partnerIds,productType",
    }
    url = httpx.URL(f"{COURSERA_API_BASE}/onDemandSpecializations.v1", params=params)
    async with httpx.AsyncClient() as client:
        data = await _api_get(client, str(url))
    elements = data.get("elements", [])
    return {
        "query": query,
        "count": len(elements),
        "specializations": [
            {
                "id": s.get("id"),
                "name": s.get("name"),
                "slug": s.get("slug"),
                "description": str(s.get("description", ""))[:300],
            }
            for s in elements
        ],
    }


async def tool_get_enrolled_courses(args: Dict[str, Any], token: Optional[str]) -> Dict[str, Any]:
    """
    List the calling user's enrolled Coursera courses.

    REQUIRES a valid Coursera OAuth 2.0 Bearer token.
    Pass it via Authorization: Bearer <token> header or configure OAuth
    in the Claude.ai connector Advanced settings.

    NOTE: Coursera's learner API endpoints are not publicly documented and
    may require partner-level API access. This implementation targets the
    /api/memberships.v1 endpoint which returns a user's enrolled programs.
    Availability depends on your Coursera developer approval status.
    """
    if not token:
        return {
            "error": "Authentication required. Provide a Coursera OAuth 2.0 Bearer token.",
            "how_to_get_token": (
                "1. Register a Coursera OAuth app (partner program required). "
                "2. Perform Authorization Code flow. "
                "3. Pass the access_token as Bearer in the Authorization header. "
                "4. Alternatively, configure OAuth in Claude.ai connector Advanced settings."
            ),
        }
    url = f"{COURSERA_API_BASE}/memberships.v1?includes=courseId,enrolledAt&fields=courseId,enrolledAt"
    async with httpx.AsyncClient() as client:
        try:
            data = await _api_get(client, url, token=token)
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 403:
                return {
                    "error": "Access denied. Your OAuth app may not have the required Coursera partner-level permissions.",
                    "status": 403,
                }
            raise
    return {"enrolled": data.get("elements", []), "raw": data}


# ──────────────────────────────────────────────
# MCP tool registry
# ──────────────────────────────────────────────

TOOLS = [
    {
        "name": "coursera_search_courses",
        "description": (
            "Search the Coursera catalog for courses matching a query. "
            "Returns course names, slugs, workload, and domain types. "
            "No authentication required."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search keywords."},
                "limit": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 20,
                    "default": 5,
                    "description": "Max results to return.",
                },
            },
            "required": ["query"],
        },
    },
    {
        "name": "coursera_get_course",
        "description": (
            "Get full metadata for a single Coursera course: description, skills, "
            "level, workload, certificates. Accepts a course slug, numeric id, or URL. "
            "No authentication required."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "course": {
                    "type": "string",
                    "description": "Course slug (e.g. 'machine-learning'), numeric id, or full Coursera URL.",
                }
            },
            "required": ["course"],
        },
    },
    {
        "name": "coursera_search_specializations",
        "description": (
            "Search the Coursera catalog for specializations (multi-course programs). "
            "No authentication required."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search keywords."},
                "limit": {"type": "integer", "minimum": 1, "maximum": 20, "default": 5},
            },
            "required": ["query"],
        },
    },
    {
        "name": "coursera_get_enrolled_courses",
        "description": (
            "List the courses the authenticated Coursera user is enrolled in. "
            "REQUIRES a valid Coursera OAuth 2.0 Bearer token — see connector Advanced settings. "
            "Note: actual course content (videos, readings) is NOT accessible via Coursera's API; "
            "this returns enrollment metadata only."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
]

TOOL_HANDLERS = {
    "coursera_search_courses": tool_search_courses,
    "coursera_get_course": tool_get_course,
    "coursera_search_specializations": tool_search_specializations,
    "coursera_get_enrolled_courses": tool_get_enrolled_courses,
}


# ──────────────────────────────────────────────
# MCP JSON-RPC request handling
# ──────────────────────────────────────────────

def _mcp_result(msg_id: Any, result: Any) -> Dict[str, Any]:
    return {"jsonrpc": "2.0", "id": msg_id, "result": result}


def _mcp_error(msg_id: Any, code: int, message: str, data: Any = None) -> Dict[str, Any]:
    err: Dict[str, Any] = {"code": code, "message": message}
    if data is not None:
        err["data"] = data
    return {"jsonrpc": "2.0", "id": msg_id, "error": err}


def _tool_content(payload: Any) -> Dict[str, Any]:
    return {
        "content": [
            {
                "type": "text",
                "text": json.dumps(payload, ensure_ascii=False, indent=2),
            }
        ]
    }


async def _handle_mcp_message(
    msg: Dict[str, Any],
    token: Optional[str],
) -> Optional[Dict[str, Any]]:
    """Route a single MCP JSON-RPC message to the appropriate handler."""
    method = msg.get("method")
    msg_id = msg.get("id")
    params = msg.get("params") or {}

    if method == "initialize":
        return _mcp_result(
            msg_id,
            {
                "protocolVersion": "2024-11-05",
                "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
                "capabilities": {"tools": {}},
                "instructions": (
                    "Coursera MCP connector. Public tools (search_courses, get_course, "
                    "search_specializations) work without auth. get_enrolled_courses requires "
                    "a Coursera OAuth 2.0 Bearer token configured in connector Advanced settings. "
                    "IMPORTANT: Coursera Plus course *content* (videos, readings, quizzes) is NOT "
                    "accessible via API — only enrollment metadata and catalog data are available."
                ),
            },
        )

    if method == "notifications/initialized":
        return None  # notification, no response expected

    if method == "tools/list":
        return _mcp_result(msg_id, {"tools": TOOLS})

    if method == "tools/call":
        name = params.get("name")
        args = params.get("arguments") or {}
        handler = TOOL_HANDLERS.get(name)
        if handler is None:
            return _mcp_error(msg_id, -32601, f"Unknown tool: {name}")
        try:
            result = await handler(args, token)
            return _mcp_result(msg_id, _tool_content(result))
        except httpx.HTTPStatusError as exc:
            return _mcp_error(msg_id, -32000, f"Coursera API error {exc.response.status_code}", str(exc))
        except Exception as exc:  # noqa: BLE001
            return _mcp_error(msg_id, -32000, str(exc), traceback.format_exc())

    if msg_id is not None:
        return _mcp_error(msg_id, -32601, f"Method not found: {method}")

    return None  # unknown notification, ignore


# ──────────────────────────────────────────────
# HTTP endpoints
# ──────────────────────────────────────────────

@app.get("/health")
async def health() -> JSONResponse:
    """Simple liveness probe — useful for deployment platforms."""
    return JSONResponse({"status": "ok", "server": SERVER_NAME, "version": SERVER_VERSION})


@app.post("/mcp")
async def mcp_endpoint(
    request: Request,
    authorization: Optional[str] = Header(default=None),
) -> JSONResponse:
    """
    MCP Streamable HTTP transport endpoint.

    Accepts a single JSON-RPC message or a JSON array of messages (batch).
    Extracts the Coursera Bearer token from the Authorization header so
    authenticated tools can use it.

    Claude.ai sends requests here after the connector URL is registered.
    """
    # Extract Bearer token if present (forwarded from Claude.ai connector auth)
    token: Optional[str] = None
    if authorization and authorization.lower().startswith("bearer "):
        token = authorization[7:].strip()

    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Request body must be valid JSON.")

    # Handle batch (array) or single message
    is_batch = isinstance(body, list)
    messages = body if is_batch else [body]

    responses = []
    for msg in messages:
        try:
            resp = await _handle_mcp_message(msg, token)
        except Exception as exc:  # noqa: BLE001
            resp = _mcp_error(msg.get("id"), -32000, str(exc), traceback.format_exc())
        if resp is not None:
            responses.append(resp)

    if not responses:
        return JSONResponse(content=None, status_code=204)

    return JSONResponse(content=responses if is_batch else responses[0])


@app.get("/mcp")
async def mcp_sse_info() -> JSONResponse:
    """
    Informational endpoint for GET /mcp.
    Full SSE server-push is not implemented here because Claude.ai's
    remote connector protocol uses synchronous POST for all tool calls.
    """
    return JSONResponse(
        {
            "message": "Coursera Remote MCP server is running.",
            "post_endpoint": "/mcp",
            "protocol": "MCP Streamable HTTP (2024-11-05)",
        }
    )


# ──────────────────────────────────────────────
# Dev entry point
# ──────────────────────────────────────────────
if __name__ == "__main__":
    import uvicorn

    uvicorn.run("coursera_remote_mcp_server:app", host="0.0.0.0", port=8000, reload=True)
