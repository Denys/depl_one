#!/usr/bin/env python3
"""Independent Coursera MCP connector (stdio JSON-RPC server).

Implements a minimal subset of MCP so it can be consumed by MCP-compatible IDEs
(e.g., VS Code with MCP support, Claude Desktop, and other clients).
"""

from __future__ import annotations

import json
import sys
import traceback
import urllib.parse
import urllib.request
from typing import Any, Dict, Optional

SERVER_NAME = "coursera-connector"
SERVER_VERSION = "0.1.0"
API_BASE = "https://api.coursera.org/api"


def _http_get_json(url: str, timeout: int = 20) -> Dict[str, Any]:
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "coursera-mcp-connector/0.1 (+https://modelcontextprotocol.io)",
            "Accept": "application/json",
        },
        method="GET",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read().decode("utf-8", errors="replace")
    return json.loads(raw)


def coursera_search(query: str, limit: int = 5) -> Dict[str, Any]:
    params = urllib.parse.urlencode(
        {
            "q": "search",
            "query": query,
            "limit": max(1, min(limit, 20)),
            "fields": "name,slug,photoUrl,partnerIds,primaryLanguages,certificates,workload,domainTypes",
        }
    )
    url = f"{API_BASE}/courses.v1?{params}"
    payload = _http_get_json(url)

    elements = payload.get("elements", [])
    compact = [
        {
            "id": c.get("id"),
            "name": c.get("name"),
            "slug": c.get("slug"),
            "languages": c.get("primaryLanguages"),
            "certificates": c.get("certificates"),
            "workload": c.get("workload"),
            "photoUrl": c.get("photoUrl"),
            "domainTypes": c.get("domainTypes"),
        }
        for c in elements
    ]
    return {"query": query, "count": len(compact), "courses": compact}


def coursera_course(course_id_or_slug: str) -> Dict[str, Any]:
    if "/" in course_id_or_slug:
        course_id_or_slug = course_id_or_slug.rsplit("/", 1)[-1]

    params = urllib.parse.urlencode(
        {
            "q": "slug",
            "slug": course_id_or_slug,
            "fields": "name,slug,description,primaryLanguages,certificates,workload,photoUrl,domainTypes,skills,level,partnerIds",
        }
    )
    url = f"{API_BASE}/courses.v1?{params}"
    payload = _http_get_json(url)
    elements = payload.get("elements", [])

    if not elements and course_id_or_slug.isdigit():
        url = f"{API_BASE}/courses.v1/{course_id_or_slug}?fields=name,slug,description,primaryLanguages,certificates,workload,photoUrl,domainTypes,skills,level,partnerIds"
        payload = _http_get_json(url)
        return payload

    if not elements:
        return {"error": f"No course found for '{course_id_or_slug}'"}

    return elements[0]


def coursera_search_specializations(query: str, limit: int = 5) -> Dict[str, Any]:
    params = urllib.parse.urlencode(
        {
            "q": "search",
            "query": query,
            "limit": max(1, min(limit, 20)),
            "fields": "name,slug,description,partnerIds,productType",
        }
    )
    url = f"{API_BASE}/onDemandSpecializations.v1?{params}"
    payload = _http_get_json(url)
    elements = payload.get("elements", [])
    compact = [
        {
            "id": s.get("id"),
            "name": s.get("name"),
            "slug": s.get("slug"),
            "description": s.get("description", "")[:280],
            "productType": s.get("productType"),
        }
        for s in elements
    ]
    return {"query": query, "count": len(compact), "specializations": compact}


def _read_message() -> Optional[Dict[str, Any]]:
    headers: Dict[str, str] = {}

    while True:
        line = sys.stdin.buffer.readline()
        if not line:
            return None

        stripped = line.strip()
        if not stripped:
            break

        key, _, value = line.decode("utf-8", errors="replace").partition(":")
        headers[key.strip().lower()] = value.strip()

    content_length = int(headers.get("content-length", "0"))
    if content_length <= 0:
        return None

    body = sys.stdin.buffer.read(content_length)
    if not body:
        return None

    return json.loads(body.decode("utf-8", errors="replace"))


def _send_message(message: Dict[str, Any]) -> None:
    encoded = json.dumps(message, ensure_ascii=False).encode("utf-8")
    header = f"Content-Length: {len(encoded)}\r\n\r\n".encode("ascii")
    sys.stdout.buffer.write(header)
    sys.stdout.buffer.write(encoded)
    sys.stdout.buffer.flush()


def _result(msg_id: Any, result: Any) -> Dict[str, Any]:
    return {"jsonrpc": "2.0", "id": msg_id, "result": result}


def _error(msg_id: Any, code: int, message: str, data: Any = None) -> Dict[str, Any]:
    payload: Dict[str, Any] = {
        "jsonrpc": "2.0",
        "id": msg_id,
        "error": {"code": code, "message": message},
    }
    if data is not None:
        payload["error"]["data"] = data
    return payload


def _handle_initialize(msg_id: Any, _params: Dict[str, Any]) -> Dict[str, Any]:
    return _result(
        msg_id,
        {
            "protocolVersion": "2024-11-05",
            "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
            "capabilities": {
                "tools": {},
            },
        },
    )


def _handle_tools_list(msg_id: Any) -> Dict[str, Any]:
    tools = [
        {
            "name": "coursera_search_courses",
            "description": "Search Coursera courses by query text.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Search terms."},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 20, "default": 5},
                },
                "required": ["query"],
            },
        },
        {
            "name": "coursera_get_course",
            "description": "Get detailed information for a Coursera course using slug or numeric id.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "course": {"type": "string", "description": "Course slug, id, or URL."}
                },
                "required": ["course"],
            },
        },
        {
            "name": "coursera_search_specializations",
            "description": "Search Coursera specializations by query text.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 20, "default": 5},
                },
                "required": ["query"],
            },
        },
    ]
    return _result(msg_id, {"tools": tools})


def _tool_result(payload: Any) -> Dict[str, Any]:
    return {"content": [{"type": "text", "text": json.dumps(payload, ensure_ascii=False, indent=2)}]}


def _handle_tool_call(msg_id: Any, params: Dict[str, Any]) -> Dict[str, Any]:
    name = params.get("name")
    args = params.get("arguments", {})

    if name == "coursera_search_courses":
        query = str(args.get("query", "")).strip()
        if not query:
            return _error(msg_id, -32602, "'query' is required")
        limit = int(args.get("limit", 5))
        return _result(msg_id, _tool_result(coursera_search(query, limit)))

    if name == "coursera_get_course":
        course = str(args.get("course", "")).strip()
        if not course:
            return _error(msg_id, -32602, "'course' is required")
        return _result(msg_id, _tool_result(coursera_course(course)))

    if name == "coursera_search_specializations":
        query = str(args.get("query", "")).strip()
        if not query:
            return _error(msg_id, -32602, "'query' is required")
        limit = int(args.get("limit", 5))
        return _result(msg_id, _tool_result(coursera_search_specializations(query, limit)))

    return _error(msg_id, -32601, f"Unknown tool: {name}")


def _handle_request(msg: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    method = msg.get("method")
    msg_id = msg.get("id")
    params = msg.get("params", {})

    if method == "initialize":
        return _handle_initialize(msg_id, params)

    if method == "notifications/initialized":
        return None

    if method == "tools/list":
        return _handle_tools_list(msg_id)

    if method == "tools/call":
        return _handle_tool_call(msg_id, params)

    if msg_id is not None:
        return _error(msg_id, -32601, f"Method not found: {method}")

    return None


def run_server() -> None:
    while True:
        msg = _read_message()
        if msg is None:
            return

        try:
            response = _handle_request(msg)
        except Exception as exc:  # noqa: BLE001
            response = _error(msg.get("id"), -32000, str(exc), traceback.format_exc())

        if response is not None:
            _send_message(response)


def self_test() -> int:
    print("Running self-test against Coursera public APIs...")
    try:
        courses = coursera_search("python", 3)
        print(json.dumps(courses, indent=2)[:700])
        specs = coursera_search_specializations("data science", 2)
        print(json.dumps(specs, indent=2)[:700])
        print("Self-test OK")
        return 0
    except Exception as exc:  # noqa: BLE001
        print(f"Self-test failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--self-test":
        raise SystemExit(self_test())
    run_server()
