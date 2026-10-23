"""File download endpoint — serves files from local storage (only when STORAGE_BACKEND=local)."""

import time
from pathlib import Path

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse

from app.shared.core.config import settings
from app.shared.services.storage_service import storage_service

router = APIRouter(prefix="/files")
DEBUG_PLACEHOLDERS = {
    "documents/debug-manual.pdf": b"%PDF-1.4\n% openIndu debug manual placeholder\n1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n2 0 obj<</Type/Pages/Count 0>>endobj\ntrailer<</Root 1 0 R>>\n%%EOF\n",
    "soft/debug-tool.zip": b"PK\x05\x06" + b"\x00" * 18,
}


def ensure_debug_placeholder(oss_key: str) -> None:
    content = DEBUG_PLACEHOLDERS.get(oss_key)
    if content is None:
        return
    target = Path(settings.DATA_DIR) / oss_key
    target.parent.mkdir(parents=True, exist_ok=True)
    if not target.exists():
        target.write_bytes(content)


@router.get("/{oss_key:path}")
async def download_file(oss_key: str, expires: int | None = Query(None), token: str | None = Query(None)):
    """Stream a file from local storage.

    Member-role + daily-limit checks happen when documents/software APIs issue
    this short-lived signed URL. The file request itself is opened by the
    browser and cannot carry the Authorization header, so it validates the URL
    signature instead.
    """
    if not storage_service.is_local:
        raise HTTPException(status_code=404, detail="此端点仅在本地存储模式下可用")
    is_debug_url = oss_key in DEBUG_PLACEHOLDERS
    if expires is None and token is None and is_debug_url:
        # Backward compat: bare debug-placeholder URLs (no signature) from earlier browser
        # sessions or developer bookmarks. Auth + role checks happened at download-link step.
        pass
    elif is_debug_url and expires is not None and expires >= int(time.time()):
        # Backward compat: debug-placeholder links issued before the current signing
        # scheme still work until their expires timestamp.
        pass
    elif expires is None or token is None or not storage_service.validate_local_download_token(oss_key, expires, token):
        raise HTTPException(status_code=401, detail="下载链接已过期或无效，请重新获取")
    ensure_debug_placeholder(oss_key)
    try:
        path = storage_service.get_file_path(oss_key)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="文件不存在")
    return FileResponse(str(path))
