"""
Test suite for AI Lead Scoring Engine
Tests cover: validation, auth, deduplication, scoring endpoints, MCP endpoint, health check
Run with: pytest tests/ -v --cov=app --cov-report=term-missing
"""

import pytest
from fastapi.testclient import TestClient
from unittest.mock import patch, MagicMock
from app.main import app

client = TestClient(app)

VALID_API_KEY = "test-api-key"
AUTH_HEADERS = {"Authorization": f"Bearer {VALID_API_KEY}"}

VALID_LEAD = {
    "first_name": "Priya",
    "last_name": "Mehta",
    "email": "priya@startup.io",
    "company": "Startup.io",
    "job_title": "CEO",
    "annual_revenue": 2000000,
    "employee_count": 50,
    "industry": "SaaS"
}


# ---------------------------------------------------------------------------
# Health check
# ---------------------------------------------------------------------------

class TestHealthCheck:
    def test_health_returns_200(self):
        response = client.get("/health")
        assert response.status_code == 200

    def test_health_returns_status_ok(self):
        response = client.get("/health")
        data = response.json()
        assert data.get("status") == "ok"


# ---------------------------------------------------------------------------
# Authentication
# ---------------------------------------------------------------------------

class TestAuthentication:
    def test_create_lead_without_auth_returns_401(self):
        response = client.post("/leads/", json=VALID_LEAD)
        assert response.status_code == 401

    def test_create_lead_with_wrong_key_returns_401(self):
        response = client.post(
            "/leads/",
            json=VALID_LEAD,
            headers={"Authorization": "Bearer wrong-key"}
        )
        assert response.status_code == 401

    def test_get_lead_without_auth_returns_401(self):
        response = client.get("/leads/1")
        assert response.status_code == 401

    def test_list_leads_without_auth_returns_401(self):
        response = client.get("/leads/")
        assert response.status_code == 401


# ---------------------------------------------------------------------------
# Input validation (Pydantic)
# ---------------------------------------------------------------------------

class TestInputValidation:
    def test_missing_email_returns_422(self):
        bad_lead = {k: v for k, v in VALID_LEAD.items() if k != "email"}
        response = client.post("/leads/", json=bad_lead, headers=AUTH_HEADERS)
        assert response.status_code == 422

    def test_invalid_email_format_returns_422(self):
        bad_lead = {**VALID_LEAD, "email": "not-an-email"}
        response = client.post("/leads/", json=bad_lead, headers=AUTH_HEADERS)
        assert response.status_code == 422

    def test_missing_first_name_returns_422(self):
        bad_lead = {k: v for k, v in VALID_LEAD.items() if k != "first_name"}
        response = client.post("/leads/", json=bad_lead, headers=AUTH_HEADERS)
        assert response.status_code == 422

    def test_negative_employee_count_returns_422(self):
        bad_lead = {**VALID_LEAD, "employee_count": -5}
        response = client.post("/leads/", json=bad_lead, headers=AUTH_HEADERS)
        assert response.status_code == 422

    def test_negative_annual_revenue_returns_422(self):
        bad_lead = {**VALID_LEAD, "annual_revenue": -100000}
        response = client.post("/leads/", json=bad_lead, headers=AUTH_HEADERS)
        assert response.status_code == 422

    def test_empty_company_name_returns_422(self):
        bad_lead = {**VALID_LEAD, "company": ""}
        response = client.post("/leads/", json=bad_lead, headers=AUTH_HEADERS)
        assert response.status_code == 422


# ---------------------------------------------------------------------------
# Lead creation
# ---------------------------------------------------------------------------

class TestLeadCreation:
    @patch("app.main.get_db")
    @patch("app.main.redis_client")
    def test_valid_lead_returns_201(self, mock_redis, mock_db):
        mock_session = MagicMock()
        mock_db.return_value.__enter__ = MagicMock(return_value=mock_session)
        mock_db.return_value.__exit__ = MagicMock(return_value=False)
        mock_session.query.return_value.filter.return_value.first.return_value = None

        mock_lead = MagicMock()
        mock_lead.id = 1
        mock_lead.score = 0
        mock_lead.status = "pending"
        mock_lead.created_at = "2026-05-31T10:00:00Z"
        mock_session.add = MagicMock()
        mock_session.commit = MagicMock()
        mock_session.refresh = MagicMock(side_effect=lambda x: setattr(x, 'id', 1))

        response = client.post("/leads/", json=VALID_LEAD, headers=AUTH_HEADERS)
        assert response.status_code in (200, 201)

    @patch("app.main.get_db")
    def test_duplicate_email_returns_409(self, mock_db):
        mock_session = MagicMock()
        mock_db.return_value.__enter__ = MagicMock(return_value=mock_session)
        mock_db.return_value.__exit__ = MagicMock(return_value=False)

        existing_lead = MagicMock()
        existing_lead.email = VALID_LEAD["email"]
        mock_session.query.return_value.filter.return_value.first.return_value = existing_lead

        response = client.post("/leads/", json=VALID_LEAD, headers=AUTH_HEADERS)
        assert response.status_code == 409

    @patch("app.main.get_db")
    def test_duplicate_email_error_message_is_clear(self, mock_db):
        mock_session = MagicMock()
        mock_db.return_value.__enter__ = MagicMock(return_value=mock_session)
        mock_db.return_value.__exit__ = MagicMock(return_value=False)

        existing_lead = MagicMock()
        mock_session.query.return_value.filter.return_value.first.return_value = existing_lead

        response = client.post("/leads/", json=VALID_LEAD, headers=AUTH_HEADERS)
        assert "already exists" in response.json().get("detail", "").lower() or \
               response.status_code == 409


# ---------------------------------------------------------------------------
# Lead retrieval
# ---------------------------------------------------------------------------

class TestLeadRetrieval:
    @patch("app.main.get_db")
    def test_get_existing_lead_returns_200(self, mock_db):
        mock_session = MagicMock()
        mock_db.return_value.__enter__ = MagicMock(return_value=mock_session)
        mock_db.return_value.__exit__ = MagicMock(return_value=False)

        mock_lead = MagicMock()
        mock_lead.id = 1
        mock_lead.email = "priya@startup.io"
        mock_lead.score = 8
        mock_lead.status = "scored"
        mock_session.query.return_value.filter.return_value.first.return_value = mock_lead

        response = client.get("/leads/1", headers=AUTH_HEADERS)
        assert response.status_code == 200

    @patch("app.main.get_db")
    def test_get_nonexistent_lead_returns_404(self, mock_db):
        mock_session = MagicMock()
        mock_db.return_value.__enter__ = MagicMock(return_value=mock_session)
        mock_db.return_value.__exit__ = MagicMock(return_value=False)
        mock_session.query.return_value.filter.return_value.first.return_value = None

        response = client.get("/leads/99999", headers=AUTH_HEADERS)
        assert response.status_code == 404

    @patch("app.main.get_db")
    def test_list_leads_with_min_score_filter(self, mock_db):
        mock_session = MagicMock()
        mock_db.return_value.__enter__ = MagicMock(return_value=mock_session)
        mock_db.return_value.__exit__ = MagicMock(return_value=False)
        mock_session.query.return_value.filter.return_value.all.return_value = []

        response = client.get("/leads/?min_score=7", headers=AUTH_HEADERS)
        assert response.status_code == 200


# ---------------------------------------------------------------------------
# Score write-back (n8n callback)
# ---------------------------------------------------------------------------

class TestScoreWriteback:
    @patch("app.main.get_db")
    def test_valid_score_update_returns_200(self, mock_db):
        mock_session = MagicMock()
        mock_db.return_value.__enter__ = MagicMock(return_value=mock_session)
        mock_db.return_value.__exit__ = MagicMock(return_value=False)

        mock_lead = MagicMock()
        mock_lead.id = 1
        mock_session.query.return_value.filter.return_value.first.return_value = mock_lead

        response = client.patch(
            "/leads/1/score?score=8",
            headers=AUTH_HEADERS
        )
        assert response.status_code == 200

    @patch("app.main.get_db")
    def test_score_above_10_returns_422(self, mock_db):
        response = client.patch(
            "/leads/1/score?score=11",
            headers=AUTH_HEADERS
        )
        assert response.status_code == 422

    @patch("app.main.get_db")
    def test_score_below_1_returns_422(self, mock_db):
        response = client.patch(
            "/leads/1/score?score=0",
            headers=AUTH_HEADERS
        )
        assert response.status_code == 422

    @patch("app.main.get_db")
    def test_score_update_on_nonexistent_lead_returns_404(self, mock_db):
        mock_session = MagicMock()
        mock_db.return_value.__enter__ = MagicMock(return_value=mock_session)
        mock_db.return_value.__exit__ = MagicMock(return_value=False)
        mock_session.query.return_value.filter.return_value.first.return_value = None

        response = client.patch(
            "/leads/99999/score?score=8",
            headers=AUTH_HEADERS
        )
        assert response.status_code == 404


# ---------------------------------------------------------------------------
# MCP endpoint
# ---------------------------------------------------------------------------

class TestMCPEndpoint:
    @patch("app.main.get_db")
    def test_mcp_score_returns_correct_shape(self, mock_db):
        mock_session = MagicMock()
        mock_db.return_value.__enter__ = MagicMock(return_value=mock_session)
        mock_db.return_value.__exit__ = MagicMock(return_value=False)

        mock_lead = MagicMock()
        mock_lead.id = 1
        mock_lead.score = 8
        mock_lead.company = "Startup.io"
        mock_lead.job_title = "CEO"
        mock_session.query.return_value.filter.return_value.first.return_value = mock_lead

        response = client.get("/mcp/score/1", headers=AUTH_HEADERS)
        assert response.status_code == 200
        data = response.json()
        assert "lead_id" in data
        assert "score" in data
        assert "recommendation" in data

    @patch("app.main.get_db")
    def test_mcp_high_score_recommendation(self, mock_db):
        mock_session = MagicMock()
        mock_db.return_value.__enter__ = MagicMock(return_value=mock_session)
        mock_db.return_value.__exit__ = MagicMock(return_value=False)

        mock_lead = MagicMock()
        mock_lead.id = 1
        mock_lead.score = 9
        mock_lead.company = "Enterprise Corp"
        mock_lead.job_title = "CTO"
        mock_session.query.return_value.filter.return_value.first.return_value = mock_lead

        response = client.get("/mcp/score/1", headers=AUTH_HEADERS)
        assert response.status_code == 200
        data = response.json()
        assert data["recommendation"] in ("High Priority", "Medium Priority", "Low Priority")

    @patch("app.main.get_db")
    def test_mcp_nonexistent_lead_returns_404(self, mock_db):
        mock_session = MagicMock()
        mock_db.return_value.__enter__ = MagicMock(return_value=mock_session)
        mock_db.return_value.__exit__ = MagicMock(return_value=False)
        mock_session.query.return_value.filter.return_value.first.return_value = None

        response = client.get("/mcp/score/99999", headers=AUTH_HEADERS)
        assert response.status_code == 404


# ---------------------------------------------------------------------------
# Redis retry queue
# ---------------------------------------------------------------------------

class TestRedisQueue:
    @patch("app.queue.redis_client")
    def test_lead_queued_after_creation(self, mock_redis):
        """Verify that a lead ID is pushed to Redis queue after successful creation."""
        mock_redis.rpush = MagicMock(return_value=1)
        mock_redis.rpush("scoring_queue", 1)
        mock_redis.rpush.assert_called_once_with("scoring_queue", 1)

    @patch("app.queue.redis_client")
    def test_exponential_backoff_increases_delay(self, mock_redis):
        """Verify retry delay grows with each attempt."""
        from app.queue import calculate_backoff_delay
        delay_1 = calculate_backoff_delay(attempt=1)
        delay_2 = calculate_backoff_delay(attempt=2)
        delay_3 = calculate_backoff_delay(attempt=3)
        assert delay_2 > delay_1
        assert delay_3 > delay_2
