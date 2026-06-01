"""
Test suite for AI Lead Scoring Engine
Tests cover: validation, auth, deduplication, scoring endpoints, MCP endpoint, health check
Run with: pytest tests/ -v --cov=app --cov-report=term-missing
"""

import os
# Set env vars BEFORE importing app — main.py reads API_KEY at import time
os.environ["API_KEY"] = "test-api-key"
os.environ["DATABASE_URL"] = "sqlite:///./test.db"
os.environ["REDIS_URL"] = "redis://localhost:6379"
os.environ["N8N_WEBHOOK_URL"] = "http://localhost:5678/webhook/test"

import pytest
from fastapi.testclient import TestClient
from unittest.mock import patch, MagicMock
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from app.main import app
from app.database import get_db
from app import models

# ---------------------------------------------------------------------------
# In-memory SQLite DB — replaces real PostgreSQL for all tests
# ---------------------------------------------------------------------------
TEST_DATABASE_URL = "sqlite:///./test.db"
engine = create_engine(TEST_DATABASE_URL, connect_args={"check_same_thread": False})
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
models.Base.metadata.create_all(bind=engine)


def override_get_db():
    db = TestingSessionLocal()
    try:
        yield db
    finally:
        db.close()


# Override FastAPI dependency — this is the correct pattern
app.dependency_overrides[get_db] = override_get_db

client = TestClient(app)

AUTH = {"Authorization": "Bearer test-api-key"}

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


# Clean up leads table between tests that write data
@pytest.fixture(autouse=True)
def clean_db():
    yield
    db = TestingSessionLocal()
    db.query(models.LeadDB).delete()
    db.commit()
    db.close()


# ---------------------------------------------------------------------------
# Health check
# ---------------------------------------------------------------------------

class TestHealthCheck:
    def test_health_returns_200(self):
        assert client.get("/health").status_code == 200

    def test_health_returns_status_ok(self):
        assert client.get("/health").json()["status"] == "ok"


# ---------------------------------------------------------------------------
# Authentication
# ---------------------------------------------------------------------------

class TestAuthentication:
    def test_create_lead_without_auth_returns_401(self):
        assert client.post("/leads/", json=VALID_LEAD).status_code == 401

    def test_create_lead_with_wrong_key_returns_401(self):
        response = client.post(
            "/leads/", json=VALID_LEAD,
            headers={"Authorization": "Bearer wrong-key"}
        )
        assert response.status_code == 401

    def test_get_lead_without_auth_returns_401(self):
        assert client.get("/leads/1").status_code == 401

    def test_list_leads_without_auth_returns_401(self):
        assert client.get("/leads/").status_code == 401


# ---------------------------------------------------------------------------
# Input validation — Pydantic constraints
# ---------------------------------------------------------------------------

class TestInputValidation:
    def test_missing_email_returns_422(self):
        bad = {k: v for k, v in VALID_LEAD.items() if k != "email"}
        assert client.post("/leads/", json=bad, headers=AUTH).status_code == 422

    def test_invalid_email_format_returns_422(self):
        assert client.post("/leads/", json={**VALID_LEAD, "email": "not-an-email"}, headers=AUTH).status_code == 422

    def test_missing_first_name_returns_422(self):
        bad = {k: v for k, v in VALID_LEAD.items() if k != "first_name"}
        assert client.post("/leads/", json=bad, headers=AUTH).status_code == 422

    def test_negative_employee_count_returns_422(self):
        assert client.post("/leads/", json={**VALID_LEAD, "employee_count": -5}, headers=AUTH).status_code == 422

    def test_negative_annual_revenue_returns_422(self):
        assert client.post("/leads/", json={**VALID_LEAD, "annual_revenue": -100000}, headers=AUTH).status_code == 422

    def test_empty_company_name_returns_422(self):
        assert client.post("/leads/", json={**VALID_LEAD, "company": ""}, headers=AUTH).status_code == 422


# ---------------------------------------------------------------------------
# Lead creation
# ---------------------------------------------------------------------------

class TestLeadCreation:
    @patch("app.main.send_to_n8n_with_retry")
    def test_valid_lead_returns_200(self, mock_retry):
        mock_retry.return_value = True
        response = client.post("/leads/", json=VALID_LEAD, headers=AUTH)
        assert response.status_code == 200

    @patch("app.main.send_to_n8n_with_retry")
    def test_valid_lead_response_has_id(self, mock_retry):
        mock_retry.return_value = True
        response = client.post("/leads/", json=VALID_LEAD, headers=AUTH)
        assert "id" in response.json()

    @patch("app.main.send_to_n8n_with_retry")
    def test_valid_lead_status_is_pending(self, mock_retry):
        mock_retry.return_value = True
        response = client.post("/leads/", json=VALID_LEAD, headers=AUTH)
        assert response.json()["status"] == "pending"

    @patch("app.main.send_to_n8n_with_retry")
    def test_duplicate_email_returns_409(self, mock_retry):
        mock_retry.return_value = True
        client.post("/leads/", json=VALID_LEAD, headers=AUTH)
        response = client.post("/leads/", json=VALID_LEAD, headers=AUTH)
        assert response.status_code == 409

    @patch("app.main.send_to_n8n_with_retry")
    def test_duplicate_error_message_mentions_exists(self, mock_retry):
        mock_retry.return_value = True
        client.post("/leads/", json=VALID_LEAD, headers=AUTH)
        response = client.post("/leads/", json=VALID_LEAD, headers=AUTH)
        assert "already exists" in response.json()["detail"].lower()


# ---------------------------------------------------------------------------
# Lead retrieval
# ---------------------------------------------------------------------------

class TestLeadRetrieval:
    @patch("app.main.send_to_n8n_with_retry")
    def test_get_existing_lead_returns_200(self, mock_retry):
        mock_retry.return_value = True
        create_resp = client.post("/leads/", json=VALID_LEAD, headers=AUTH)
        lead_id = create_resp.json()["id"]
        response = client.get(f"/leads/{lead_id}", headers=AUTH)
        assert response.status_code == 200

    def test_get_nonexistent_lead_returns_404(self):
        assert client.get("/leads/99999", headers=AUTH).status_code == 404

    @patch("app.main.send_to_n8n_with_retry")
    def test_list_leads_returns_200(self, mock_retry):
        mock_retry.return_value = True
        assert client.get("/leads/", headers=AUTH).status_code == 200

    @patch("app.main.send_to_n8n_with_retry")
    def test_list_leads_min_score_filter(self, mock_retry):
        mock_retry.return_value = True
        assert client.get("/leads/?min_score=7", headers=AUTH).status_code == 200


# ---------------------------------------------------------------------------
# Score write-back
# ---------------------------------------------------------------------------

class TestScoreWriteback:
    @patch("app.main.send_to_n8n_with_retry")
    def test_valid_score_update_returns_200(self, mock_retry):
        mock_retry.return_value = True
        create_resp = client.post("/leads/", json=VALID_LEAD, headers=AUTH)
        lead_id = create_resp.json()["id"]
        response = client.patch(f"/leads/{lead_id}/score?score=8")
        assert response.status_code == 200

    def test_score_above_10_returns_422(self):
        assert client.patch("/leads/1/score?score=11").status_code == 422

    def test_score_below_1_returns_422(self):
        assert client.patch("/leads/1/score?score=0").status_code == 422

    def test_score_nonexistent_lead_returns_404(self):
        assert client.patch("/leads/99999/score?score=8").status_code == 404


# ---------------------------------------------------------------------------
# MCP endpoint
# ---------------------------------------------------------------------------

class TestMCPEndpoint:
    @patch("app.main.send_to_n8n_with_retry")
    def test_mcp_scored_lead_returns_correct_shape(self, mock_retry):
        mock_retry.return_value = True
        create_resp = client.post("/leads/", json=VALID_LEAD, headers=AUTH)
        lead_id = create_resp.json()["id"]
        # Write a score first
        client.patch(f"/leads/{lead_id}/score?score=8")
        response = client.get(f"/mcp/score/{lead_id}", headers=AUTH)
        assert response.status_code == 200
        data = response.json()
        assert "lead_id" in data
        assert "score" in data
        assert "recommendation" in data

    @patch("app.main.send_to_n8n_with_retry")
    def test_mcp_high_score_is_high_priority(self, mock_retry):
        mock_retry.return_value = True
        create_resp = client.post("/leads/", json=VALID_LEAD, headers=AUTH)
        lead_id = create_resp.json()["id"]
        client.patch(f"/leads/{lead_id}/score?score=9")
        response = client.get(f"/mcp/score/{lead_id}", headers=AUTH)
        assert response.json()["recommendation"] == "High Priority"

    @patch("app.main.send_to_n8n_with_retry")
    def test_mcp_low_score_is_low_priority(self, mock_retry):
        mock_retry.return_value = True
        lead2 = {**VALID_LEAD, "email": "low@startup.io"}
        create_resp = client.post("/leads/", json=lead2, headers=AUTH)
        lead_id = create_resp.json()["id"]
        client.patch(f"/leads/{lead_id}/score?score=3")
        response = client.get(f"/mcp/score/{lead_id}", headers=AUTH)
        assert response.json()["recommendation"] == "Low Priority"

    def test_mcp_nonexistent_lead_returns_404(self):
        assert client.get("/mcp/score/99999", headers=AUTH).status_code == 404


# ---------------------------------------------------------------------------
# Redis / backoff
# ---------------------------------------------------------------------------

class TestRedisBackoff:
    def test_backoff_delay_increases_each_attempt(self):
        from app.queue import calculate_backoff_delay
        d1 = calculate_backoff_delay(1)
        d2 = calculate_backoff_delay(2)
        d3 = calculate_backoff_delay(3)
        assert d2 > d1
        assert d3 > d2

    def test_backoff_delay_is_positive(self):
        from app.queue import calculate_backoff_delay
        assert calculate_backoff_delay(1) > 0
