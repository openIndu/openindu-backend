"""Download audit log and daily limit counter."""
from sqlalchemy import BigInteger, Column, DateTime, String, func

from app.shared.models import Base


class DownloadLog(Base):
    __tablename__ = "download_logs"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    user_id = Column(BigInteger, nullable=False, index=True)
    resource_type = Column(String(20), nullable=False, index=True)
    resource_id = Column(BigInteger, nullable=False, index=True)
    ip_address = Column(String(64), nullable=True)
    created_at = Column(DateTime, server_default=func.now(), nullable=False, index=True)
