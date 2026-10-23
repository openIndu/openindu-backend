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
import app.shared.models.admin_audit_log  # noqa
import app.shared.models.chat_log  # noqa
import app.shared.models.document  # noqa
import app.shared.models.download_log  # noqa
import app.shared.models.login_session  # noqa
import app.shared.models.portal_content  # noqa
import app.shared.models.slider_challenge  # noqa
import app.shared.models.sms_code  # noqa
import app.shared.models.software  # noqa
import app.shared.models.sync_log  # noqa
import app.shared.models.token_blacklist  # noqa
import app.shared.models.user  # noqa
import app.shared.models.visit_event  # noqa

# Create all database tables on the app's engine (shared file)
from app.shared.core.database import engine
from app.shared.models import Base

Base.metadata.create_all(bind=engine)


@pytest.fixture(scope="function")
def temp_data_dir():
    """Temporary directory for file storage tests."""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield tmpdir
