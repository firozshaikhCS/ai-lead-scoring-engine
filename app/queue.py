import redis
import time
import random
import requests
import os
import logging

logger = logging.getLogger(__name__)

REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")
N8N_WEBHOOK_URL = os.getenv("N8N_WEBHOOK_URL", "http://localhost:5678/webhook/score")

# Use connection pool — avoids connection leak under load
try:
    r = redis.from_url(REDIS_URL, decode_responses=True)
    r.ping()
except redis.ConnectionError:
    logger.warning("Redis not available — retry queue disabled, falling back to direct call")
    r = None


def calculate_backoff_delay(attempt: int) -> float:
    """
    Exponential backoff with jitter.
    Formula: (2 ^ attempt) + random jitter 0–1 second.

    attempt=1 → ~2.x seconds
    attempt=2 → ~4.x seconds
    attempt=3 → ~8.x seconds

    Jitter prevents thundering herd: multiple failed requests retrying
    simultaneously would hammer the rate limit again.
    """
    return (2 ** attempt) + random.uniform(0, 1)


def mark_lead_failed(lead_id: int):
    """
    FIX: Previously leads stayed 'pending' forever after max retries.
    Now marks status as 'scoring_failed' so it's visible and actionable.
    """
    try:
        # Import here to avoid circular import
        from app.database import SessionLocal
        from app import models
        db = SessionLocal()
        lead = db.query(models.LeadDB).filter(models.LeadDB.id == lead_id).first()
        if lead:
            lead.status = "scoring_failed"
            db.commit()
            logger.error(f"Lead {lead_id} marked as scoring_failed — check n8n connection")
        db.close()
    except Exception as e:
        logger.error(f"Could not mark lead {lead_id} as failed: {e}")


def send_to_n8n_with_retry(lead_id: int, max_retries: int = 5) -> bool:
    """
    Send lead_id to n8n webhook with exponential backoff + jitter.
    On permanent failure: marks lead status as 'scoring_failed'.
    """
    for attempt in range(max_retries):
        try:
            response = requests.post(
                N8N_WEBHOOK_URL,
                json={"lead_id": lead_id},
                timeout=10
            )
            response.raise_for_status()
            logger.info(f"Lead {lead_id} sent to n8n on attempt {attempt + 1}")
            return True

        except requests.exceptions.Timeout:
            logger.warning(f"Timeout on attempt {attempt + 1} for lead {lead_id}")
            time.sleep(calculate_backoff_delay(attempt))

        except requests.exceptions.HTTPError as e:
            status = e.response.status_code if e.response else 0
            if status == 429 or status >= 500:
                sleep_time = calculate_backoff_delay(attempt)
                logger.warning(f"HTTP {status} — backing off {sleep_time:.2f}s")
                time.sleep(sleep_time)
            else:
                logger.error(f"Non-retryable HTTP error {status} for lead {lead_id}")
                mark_lead_failed(lead_id)
                return False

        except requests.exceptions.ConnectionError:
            sleep_time = calculate_backoff_delay(attempt)
            logger.warning(f"Connection error — backing off {sleep_time:.2f}s")
            time.sleep(sleep_time)

    # All retries exhausted
    mark_lead_failed(lead_id)
    return False
