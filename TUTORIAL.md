# Custom Connector Tutorial: Coursera MCP for Claude.ai

A step-by-step guide to understanding, building, and registering a Remote MCP
server as a custom connector in Claude.ai. Uses the Coursera integration as a
concrete example. Treat this as a template for any future connector.

---

## Part 0: Analysis — Can Claude Access Coursera Plus Content?

**Short answer: Partially, and not in the way you might hope.**

| What you want | Feasible? | Notes |
|---|---|---|
| Search catalog / course metadata | **Yes** | Public API, no auth needed |
| List your enrolled courses | **Partial** | Requires OAuth + Coursera partner approval |
| Video transcripts / subtitles | **No** | Not exposed via any public API |
| Reading materials (HTML/PDF) | **No** | CDN-protected, no API |
| Quiz / assignment content | **No** | No public API |
| Discussion forum posts | **No** | No public API for third parties |

**Why username/password is the wrong approach:**
- Embedding credentials in a server you deploy is a security liability.
- Coursera's API does not accept username/password; it uses OAuth 2.0.
- Even with OAuth, content behind the Plus paywall (videos, readings) is
  served from a CDN, not from a JSON API.

**What OAuth actually gives you:**
- An access token representing your user session.
- Access to enrollment metadata (which courses you are enrolled in).
- Access to the same public catalog data (same as no-auth endpoints).

**Practical workaround for course context:**
If your goal is "give Claude context from a specific course":
1. Download transcripts/subtitles from the Coursera UI (Settings → Subtitles).
2. Paste them into a Claude Project knowledge base, or upload as a file.
3. Use the connector for discovery (search, course details) and let the
   uploaded materials provide the actual content.

---

## Part 1: Concepts

### 1.1 stdio vs Remote MCP

| | stdio (local) | Remote MCP (HTTP) |
|---|---|---|
| Where it runs | On your machine | On any HTTPS server |
| Used by | Claude Desktop, VS Code | Claude.ai web interface |
| How to configure | `claude_desktop_config.json` / `.vscode/mcp.json` | Connector URL in claude.ai UI |
| Auth model | Env vars / local secrets | Bearer token / OAuth 2.0 |

The existing `coursera_mcp_server.py` in this repo is a stdio server.
`coursera_remote_mcp_server.py` is the HTTP server required for Claude.ai.

### 1.2 MCP Message Flow

```
Claude.ai  →  POST /mcp  →  Your server
           ←  JSON-RPC response  ←
```

Every interaction is a JSON-RPC 2.0 message. Four methods matter:

| Method | Direction | Purpose |
|---|---|---|
| `initialize` | Claude → Server | Handshake; server returns capabilities |
| `notifications/initialized` | Claude → Server | Confirmation notification (no reply) |
| `tools/list` | Claude → Server | Claude discovers available tools |
| `tools/call` | Claude → Server | Claude invokes a tool |

### 1.3 Tool Schema

Each tool you expose has three fields:

```json
{
  "name": "my_tool",
  "description": "What this tool does — Claude reads this to decide when to call it.",
  "inputSchema": {
    "type": "object",
    "properties": {
      "param1": { "type": "string", "description": "..." }
    },
    "required": ["param1"]
  }
}
```

---

## Part 2: Building a Remote MCP Server

### 2.1 Prerequisites

```bash
pip install fastapi "uvicorn[standard]" httpx python-dotenv
```

### 2.2 Minimal server skeleton

```python
from fastapi import FastAPI, Header, Request
from fastapi.responses import JSONResponse
from typing import Any, Dict, Optional
import json, traceback

app = FastAPI()

TOOLS = [
    {
        "name": "my_tool",
        "description": "Does something useful.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "input": {"type": "string"}
            },
            "required": ["input"]
        }
    }
]

async def handle_my_tool(args: Dict, token: Optional[str]) -> Dict:
    return {"result": f"You sent: {args.get('input')}"}

TOOL_HANDLERS = {"my_tool": handle_my_tool}

def ok(msg_id, result):
    return {"jsonrpc": "2.0", "id": msg_id, "result": result}

def err(msg_id, code, message):
    return {"jsonrpc": "2.0", "id": msg_id, "error": {"code": code, "message": message}}

@app.post("/mcp")
async def mcp(request: Request, authorization: Optional[str] = Header(default=None)):
    token = authorization[7:] if authorization and authorization.lower().startswith("bearer ") else None
    body = await request.json()

    method = body.get("method")
    msg_id = body.get("id")
    params = body.get("params") or {}

    if method == "initialize":
        return JSONResponse(ok(msg_id, {
            "protocolVersion": "2024-11-05",
            "serverInfo": {"name": "my-connector", "version": "1.0.0"},
            "capabilities": {"tools": {}}
        }))

    if method == "notifications/initialized":
        return JSONResponse(content=None, status_code=204)

    if method == "tools/list":
        return JSONResponse(ok(msg_id, {"tools": TOOLS}))

    if method == "tools/call":
        name = params.get("name")
        handler = TOOL_HANDLERS.get(name)
        if not handler:
            return JSONResponse(err(msg_id, -32601, f"Unknown tool: {name}"))
        result = await handler(params.get("arguments") or {}, token)
        return JSONResponse(ok(msg_id, {
            "content": [{"type": "text", "text": json.dumps(result, indent=2)}]
        }))

    return JSONResponse(err(msg_id, -32601, f"Unknown method: {method}"))
```

### 2.3 Run locally

```bash
uvicorn my_server:app --port 8000 --reload
```

Test it manually:

```bash
curl -X POST http://localhost:8000/mcp \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{}}}'
```

Expected response:

```json
{
  "jsonrpc": "2.0",
  "id": 1,
  "result": {
    "protocolVersion": "2024-11-05",
    "serverInfo": {"name": "my-connector", "version": "1.0.0"},
    "capabilities": {"tools": {}}
  }
}
```

---

## Part 3: Authentication

### 3.1 No auth (public APIs)

Skip the token extraction; ignore the `Authorization` header entirely.

### 3.2 Static API key

User sets a fixed API key in connector Advanced settings:

```
Authorization: Bearer <your-static-key>
```

Your server validates it:

```python
SECRET_KEY = os.getenv("CONNECTOR_SECRET_KEY")

@app.post("/mcp")
async def mcp(request: Request, authorization: Optional[str] = Header(default=None)):
    if authorization != f"Bearer {SECRET_KEY}":
        raise HTTPException(status_code=401, detail="Invalid key")
    ...
```

### 3.3 OAuth 2.0 (for user-identity APIs like Coursera)

Claude.ai's connector "Advanced settings" supports OAuth 2.0. The flow:

```
User opens connector in Claude.ai
    ↓
Claude.ai redirects user to your Authorization URL
    ↓
User logs in to the third-party service (e.g. Coursera)
    ↓
Third-party redirects back with an authorization code
    ↓
Claude.ai exchanges the code for an access token using your Token URL
    ↓
Claude.ai injects: Authorization: Bearer <access_token> on every request
    ↓
Your MCP server uses the token to call the third-party API on behalf of the user
```

**Configuration in Claude.ai Advanced settings:**

| Field | Value |
|---|---|
| Authorization URL | `https://accounts.coursera.org/oauth2/v1/auth` |
| Token URL | `https://accounts.coursera.org/oauth2/v1/token` |
| Client ID | Your Coursera OAuth app client ID |
| Client Secret | Your Coursera OAuth app client secret |
| Scope | `view_profile` (or whatever Coursera grants) |

Your server receives the token already extracted — no OAuth plumbing needed
in the server code itself:

```python
async def tool_enrolled(args, token):
    if not token:
        return {"error": "Auth required. Configure OAuth in connector settings."}
    resp = await httpx.AsyncClient().get(
        "https://api.coursera.org/api/memberships.v1",
        headers={"Authorization": f"Bearer {token}"}
    )
    return resp.json()
```

---

## Part 4: Deploying for Production

The connector URL **must be HTTPS** for Claude.ai. Options:

### Option A: Railway (easiest)

```bash
# Install Railway CLI
npm install -g @railway/cli
railway login
railway init
railway up
# Railway gives you: https://your-app.up.railway.app
```

### Option B: Fly.io

```bash
fly launch --name my-mcp-server
fly deploy
# URL: https://my-mcp-server.fly.dev
```

### Option C: Render

1. Push your repo to GitHub.
2. Create a new Web Service on render.com.
3. Set build command: `pip install -r requirements.txt`
4. Set start command: `uvicorn coursera_remote_mcp_server:app --host 0.0.0.0 --port $PORT`
5. Render gives you an HTTPS URL automatically.

### Option D: ngrok (temporary, for development only)

```bash
pip install uvicorn
uvicorn coursera_remote_mcp_server:app --port 8000
# In another terminal:
ngrok http 8000
# Use the https://xxxxx.ngrok-free.app URL in Claude.ai
```

---

## Part 5: Registering in Claude.ai

1. Go to **claude.ai** → top-left menu → **Customize**.
2. Select **Connectors** → **+** (Add custom connector).
3. Fill in:
   - **Name**: `Coursera`
   - **Remote MCP server URL**: `https://your-server.example.com/mcp`
4. Expand **Advanced settings** if you need OAuth:
   - Set Authorization URL, Token URL, Client ID, Client Secret, Scope.
5. Click **Add**.
6. Claude will now list your tools when you start a conversation.

---

## Part 6: Testing the Coursera Connector

```bash
# 1. Install deps
pip install fastapi "uvicorn[standard]" httpx python-dotenv

# 2. Start the server
uvicorn coursera_remote_mcp_server:app --port 8000 --reload

# 3. Check health
curl http://localhost:8000/health

# 4. Initialize handshake
curl -X POST http://localhost:8000/mcp \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{}}}'

# 5. List tools
curl -X POST http://localhost:8000/mcp \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","id":2,"method":"tools/list","params":{}}'

# 6. Call a public tool
curl -X POST http://localhost:8000/mcp \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","id":3,"method":"tools/call","params":{"name":"coursera_search_courses","arguments":{"query":"python","limit":3}}}'

# 7. Call an auth-gated tool (will return descriptive error without token)
curl -X POST http://localhost:8000/mcp \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer YOUR_COURSERA_TOKEN" \
  -d '{"jsonrpc":"2.0","id":4,"method":"tools/call","params":{"name":"coursera_get_enrolled_courses","arguments":{}}}'
```

---

## Part 7: Checklist for Future Connectors

Use this when building a new connector from scratch:

- [ ] Identify what API you are wrapping (REST, GraphQL, etc.)
- [ ] Identify auth model (none / API key / OAuth 2.0)
- [ ] Design 3–8 focused tools with clear descriptions (Claude reads these)
- [ ] Implement `POST /mcp` handling all four MCP methods
- [ ] Add `GET /health` for deployment platform liveness checks
- [ ] Add CORS middleware (`allow_origins=["https://claude.ai"]`)
- [ ] Extract Bearer token from `Authorization` header
- [ ] Return descriptive errors when auth is missing or invalid
- [ ] Test locally with `curl` before deploying
- [ ] Deploy to HTTPS host
- [ ] Register URL in Claude.ai → Customize → Connectors
- [ ] Configure OAuth in Advanced settings if needed
- [ ] Verify tools appear and respond correctly in a Claude conversation

---

## Files in this Repository

| File | Purpose |
|---|---|
| `coursera_mcp_server.py` | **stdio** server — for Claude Desktop and VS Code |
| `coursera_remote_mcp_server.py` | **HTTP** server — for Claude.ai web custom connectors |
| `TUTORIAL.md` | This tutorial |

---

## Coursera Developer Access

Coursera's partner API requires approval. Start here:
- Partner program: https://partner.coursera.help/hc/en-us/articles/209819543
- Without approval, the public catalog tools in this connector work fully.
- With approval, the enrolled-courses tool becomes operational.
