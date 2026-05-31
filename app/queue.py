import redis
import time
import random
import requests
import os
import logging

logger = logging.getLogger(__name__)

REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")
N8N_WEBHOOK_URL = os.getenv("N8N_WEBHOOK_URL", "http://localhost:5678/webhook/score")

# GAP FIX: Use connection pool, not bare Redis() — avoids connection leak under load
try:
    r = redis.from_url(REDIS_URL, decode_responses=True)
    r.ping()
except redis.ConnectionError:
    logger.warning("Redis not available — retry queue disabled, falling back to direct call")
    r = None


def calculate_backoff_delay(attempt: int) -> float:
    """
    Returns exponential backoff delay with jitter for a given attempt number.
    Formula: (2 ^ attempt) + random jitter between 0 and 1 second.

    attempt=1 -> ~2.x seconds
    attempt=2 -> ~4.x seconds
    attempt=3 -> ~8.x seconds

    Jitter prevents thundering herd: multiple failed requests retrying
    simultaneously would hammer the rate limit again. Spreading them
    randomly across a window lets the API recover.
    """
    return (2 ** attempt) + random.uniform(0, 1)


def send_to_n8n_with_retry(lead_id: int, max_retries: int = 5) -> bool:
    """
    Send lead_id to n8n webhook with exponential backoff + jitter.
    GAP FIX: Original guide had incomplete error handling — only caught 429,
    not network errors, timeouts, or 5xx responses.
    """
    for attempt in range(max_retries):
        try:
            response = requests.post(
                N8N_WEBHOOK_URL,
                json={"lead_id": lead_id},
                timeout=10  # GAP FIX: original had no timeout — hangs forever on dead n8n
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
                # 4xx client errors won't fix themselves — stop retrying
                logger.error(f"Non-retryable HTTP error {status} for lead {lead_id}")
                return False

        except requests.exceptions.ConnectionError:
            sleep_time = calculate_backoff_delay(attempt)
            logger.warning(f"Connection error — backing off {sleep_time:.2f}s")
            time.sleep(sleep_time)

    logger.error(f"All {max_retries} attempts failed for lead {lead_id}")
    return False
