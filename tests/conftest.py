"""Shared test fixtures for unit and integration tests."""
import os
import tempfile

import pytest

# Use a shared temp file so ALL engines (conftest, middleware, etc.) connect to the SAME database
TEST_DB_PATH = os.path.join(tempfile.gettempdir(), "openindu_test.db")
os.environ["DATABASE_URL"] = f"sqlite:///{TEST_DB_PATH}"

# Remove stale test db from previous runs
if os.path.exists(TEST_DB_PATH):
    try:
        os.remove(TEST_DB_PATH)
    except OSError:
        pass

# Now import app modules - they'll use the shared file database
# Import ALL model modules so they register with Base.metadata
import app.models.admin_audit_log  # noqa
import app.models.chat_log  # noqa
import app.models.document  # noqa
import app.models.download_log  # noqa
import app.models.login_session  # noqa
import app.models.portal_content  # noqa
import app.models.slider_challenge  # noqa
import app.models.sms_code  # noqa
import app.models.software  # noqa
import app.models.sync_log  # noqa
import app.models.token_blacklist  # noqa
import app.models.user  # noqa
import app.models.visit_event  # noqa

# Create all database tables on the app's engine (shared file)
from app.core.database import engine
from app.models import Base

Base.metadata.create_all(bind=engine)


@pytest.fixture(scope="function")
def temp_data_dir():
    """Temporary directory for file storage tests."""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield tmpdir
