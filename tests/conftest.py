"""
Pytest configuration and shared fixtures.
"""

import pytest
import os

os.environ.setdefault("API_KEY", "test-api-key")
os.environ.setdefault("DATABASE_URL", "sqlite:///./test.db")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379")
os.environ.setdefault("N8N_WEBHOOK_URL", "http://localhost:5678/webhook/test")
