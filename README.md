# Coursera Connector (Independent MCP Server)

This repository contains an **independent connector** you can run locally to expose Coursera catalog data to MCP-capable IDEs/models (including VS Code MCP clients).

## What I could verify about the ChatGPT "Coursera" button

From the UI alone, we can verify there is an app/integration entrypoint named **Coursera** in ChatGPT's tools menu. We **cannot** directly verify whether OpenAI implemented it via MCP, an internal app framework, or a private connector protocol because that transport is not publicly exposed in the UI.

Practically, integrations like this usually map to one of these patterns:

1. **Internal app/plugin runtime** (private protocol, server-side OAuth + tool wrappers).
2. **MCP-compatible bridge** (tools surfaced in a standard schema).
3. **Direct API connector** wrapped by product-specific orchestration.

So: we can verify the feature exists, but not the exact private wire protocol from screenshot evidence alone.

## What this project provides

- `coursera_mcp_server.py`: a standalone MCP stdio server.
- Tools:
  - `coursera_search_courses`
  - `coursera_get_course`
  - `coursera_search_specializations`
- Uses Coursera public catalog endpoints (`api.coursera.org`) and returns structured JSON as tool content.

## Quick start

```bash
python3 coursera_mcp_server.py --self-test
```

Then run as MCP server via stdio:

```bash
python3 coursera_mcp_server.py
```

## VS Code MCP example

Create `.vscode/mcp.json` in your workspace:

```json
{
  "servers": {
    "coursera": {
      "command": "python3",
      "args": ["/absolute/path/to/coursera_mcp_server.py"]
    }
  }
}
```

If your IDE supports MCP server discovery, it should expose the three tools above to any compatible model.

## Notes

- This connector is independent and not affiliated with OpenAI or Coursera.
- Public endpoint fields may evolve; adjust `fields=` in the script if needed.
- Respect Coursera terms of use and rate limits.
