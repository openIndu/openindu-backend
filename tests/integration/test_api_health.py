"""Integration tests for health endpoint."""
import pytest


class TestHealthEndpoint:
    """Test the /api/v1/health endpoint."""

    @pytest.fixture(autouse=True)
    def setup(self):
        from fastapi.testclient import TestClient

        from app.web_app import app
        self.client = TestClient(app)

    def test_health_returns_200(self):
        """Health endpoint should return 200 with service info."""
        response = self.client.get("/api/v1/health")
        assert response.status_code == 200
        data = response.json()
        assert data["code"] == 200
        assert data["message"] == "ok"
        assert data["data"]["service"] == "openIndu-backend-web"

    def test_health_response_format(self):
        """Health response should have the standard ok() format."""
        response = self.client.get("/api/v1/health")
        data = response.json()
        assert "code" in data
        assert "message" in data
        assert "data" in data
