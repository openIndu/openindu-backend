"""Alembic environment."""
from logging.config import fileConfig

from sqlalchemy import engine_from_config, pool

from alembic import context
from app.shared.core.config import settings
from app.shared.models import Base
from app.shared.models.admin_audit_log import AdminAuditLog  # noqa: F401
from app.shared.models.brand_mapping import BrandMapping  # noqa: F401
from app.shared.models.document import Document  # noqa: F401
from app.shared.models.download_log import DownloadLog  # noqa: F401
from app.shared.models.login_session import LoginSession  # noqa: F401
from app.shared.models.portal_content import PortalContent  # noqa: F401
from app.shared.models.sms_code import SmsCode  # noqa: F401
from app.shared.models.software import Software, SoftwareVersion  # noqa: F401
from app.shared.models.sync_log import SyncLog  # noqa: F401
from app.shared.models.token_blacklist import TokenBlacklist  # noqa: F401
from app.shared.models.user import User  # noqa: F401

config = context.config
config.set_main_option("sqlalchemy.url", settings.DATABASE_URL)

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    context.configure(url=settings.DATABASE_URL, target_metadata=target_metadata, literal_binds=True, dialect_opts={"paramstyle": "named"})
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(config.get_section(config.config_ini_section, {}), prefix="sqlalchemy.", poolclass=pool.NullPool)
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
