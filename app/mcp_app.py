"""FastAPI MCP application on port 8005."""
from fastapi import FastAPI, HTTPException

from app.mcp.server import mcp
from app.shared.core.config import settings

app = FastAPI(title="openIndu Backend MCP API", version="0.1.0")


@app.middleware("http")
async def api_key_auth(request, call_next):
    if request.url.path not in ("/health", "/api/v1/health") and not request.url.path.startswith("/mcp"):
        api_key = request.headers.get("x-api-key")
        if settings.MCP_API_KEY and api_key != settings.MCP_API_KEY:
            raise HTTPException(status_code=401, detail="Invalid MCP API key")
    return await call_next(request)


@app.get("/health")
async def health():
    return {"code": 200, "message": "ok", "data": {"service": "openIndu-backend-mcp"}}


# Mount the MCP SSE server at /mcp
# mount_path must be "" here because FastAPI already mounts the sub-app at /mcp;
# setting mount_path="/mcp" would make session URLs double-prefix to /mcp/mcp/messages/
mcp_sse_app = mcp.sse_app(mount_path="")
app.mount("/mcp", mcp_sse_app)

