"""FastAPI Web REST application on port 8004."""
import logging
from contextlib import asynccontextmanager
from datetime import UTC, datetime

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from slowapi import Limiter
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware
from slowapi.util import get_remote_address
from starlette.responses import JSONResponse

from app.api import (
    admin,
    auth,
    brand_mapping,
    chat,
    chat_sessions,
    documents,
    files,
    member_applications,
    portal,
    software,
    stats,
    tags,
    users,
    visits,
)
from app.core.config import settings
from app.core.database import engine
from app.core.err_code import ErrorCode
from app.domain.auth import AuthError
from app.domain.catalog import CatalogError
from app.domain.knowledge import KnowledgeError
from app.domain.membership import MemberError
from app.middleware.admin_audit import AdminAuditMiddleware
from app.middleware.online_stats import OnlineStatsMiddleware
from app.middleware.token_blacklist import TokenBlacklistMiddleware
from app.models import Base
from app.models.admin_audit_log import AdminAuditLog  # noqa: F401
from app.models.chat_log import ChatLog  # noqa: F401
from app.models.chat_message import ChatMessage  # noqa: F401
from app.models.chat_session import ChatSession  # noqa: F401
from app.models.document import Document  # noqa: F401
from app.models.download_log import DownloadLog  # noqa: F401
from app.models.login_session import LoginSession  # noqa: F401
from app.models.portal_content import PortalContent  # noqa: F401
from app.models.resource_tag import ResourceTag  # noqa: F401
from app.models.sms_code import SmsCode  # noqa: F401
from app.models.software import Software, SoftwareVersion  # noqa: F401
from app.models.sync_log import SyncLog  # noqa: F401
from app.models.token_blacklist import TokenBlacklist  # noqa: F401
from app.models.user import User  # noqa: F401
from app.models.visit_event import VisitEvent  # noqa: F401
from app.tasks.maintenance_task import MaintenanceScheduler

logger = logging.getLogger(__name__)
limiter = Limiter(key_func=get_remote_address, default_limits=["50/second"])
scheduler = MaintenanceScheduler()


def _init_milvus_collection() -> None:
    """Create the plc_knowledge Milvus collection if it does not exist."""
    try:
        from pymilvus import Collection, CollectionSchema, DataType, FieldSchema, connections, utility

        connections.connect(alias="default", host=settings.MILVUS_HOST, port=settings.MILVUS_PORT, timeout=5)
        if utility.has_collection(settings.MILVUS_COLLECTION):
            logger.info("Milvus collection '%s' already exists", settings.MILVUS_COLLECTION)
            return
        fields = [
            FieldSchema(name="id", dtype=DataType.INT64, is_primary=True, auto_id=True),
            FieldSchema(name="text", dtype=DataType.VARCHAR, max_length=2048),
            FieldSchema(name="document_name", dtype=DataType.VARCHAR, max_length=500),
            FieldSchema(name="brand", dtype=DataType.VARCHAR, max_length=50),
            FieldSchema(name="category", dtype=DataType.VARCHAR, max_length=50),
            FieldSchema(name="page", dtype=DataType.INT32),
            FieldSchema(name="chunk_id", dtype=DataType.INT32),
            FieldSchema(name="embedding", dtype=DataType.FLOAT_VECTOR, dim=1024),
        ]
        schema = CollectionSchema(fields=fields, description="PLC knowledge base")
        collection = Collection(name=settings.MILVUS_COLLECTION, schema=schema)
        collection.create_index(
            field_name="embedding",
            index_params={"metric_type": "COSINE", "index_type": "IVF_FLAT", "params": {"nlist": 128}},
        )
        logger.info("Milvus collection '%s' created successfully", settings.MILVUS_COLLECTION)
    except Exception as exc:
        logger.warning("Milvus collection init skipped (will retry on first sync): %s", exc)


@asynccontextmanager
async def lifespan(app: FastAPI):
    Base.metadata.create_all(engine)
    _init_milvus_collection()
    scheduler.start()
    yield
    scheduler.stop()


app = FastAPI(title="openIndu Backend Web API", version="0.1.0", lifespan=lifespan)
app.state.limiter = limiter


@app.exception_handler(RateLimitExceeded)
async def rate_limit_handler(request: Request, exc: RateLimitExceeded):
    return JSONResponse(status_code=429, content={"code": 429, "detail": "请求过于频繁"})


@app.exception_handler(AuthError)
async def auth_error_handler(request: Request, exc: AuthError):
    return JSONResponse(status_code=ErrorCode.status(exc.code), content=ErrorCode.error_body(exc.code, **exc.params))


@app.exception_handler(MemberError)
async def member_error_handler(request: Request, exc: MemberError):
    return JSONResponse(status_code=ErrorCode.status(exc.code), content=ErrorCode.error_body(exc.code, **exc.params))


@app.exception_handler(CatalogError)
async def catalog_error_handler(request: Request, exc: CatalogError):
    return JSONResponse(status_code=ErrorCode.status(exc.code), content=ErrorCode.error_body(exc.code, **exc.params))


@app.exception_handler(KnowledgeError)
async def knowledge_error_handler(request: Request, exc: KnowledgeError):
    return JSONResponse(status_code=ErrorCode.status(exc.code), content=ErrorCode.error_body(exc.code, **exc.params))


@app.exception_handler(Exception)
async def generic_exception_handler(request: Request, exc: Exception):
    return JSONResponse(status_code=500, content={"code": 500, "detail": "服务器内部错误"})


app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.add_middleware(SlowAPIMiddleware)
app.add_middleware(AdminAuditMiddleware)
app.add_middleware(OnlineStatsMiddleware)
app.add_middleware(TokenBlacklistMiddleware)

for router in [auth.router, users.router, stats.router, admin.router, documents.router, software.router, brand_mapping.router, files.router, visits.router, portal.router, tags.router, chat.router, chat_sessions.router, member_applications.router]:
    app.include_router(router, prefix="/api/v1")


@app.get("/api/v1/health")
async def health():
    return {"code": 200, "message": "ok", "data": {"service": "openIndu-backend-web", "timestamp": datetime.now(UTC).isoformat()}}
