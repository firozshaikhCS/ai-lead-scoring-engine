# Architecture Decision Records

> This document explains **why** the system is built the way it is — not just what it does.  
> Every choice here was made to solve a real production problem, not to follow a tutorial.

---

## ADR-001 — Why FastAPI instead of sending webhooks directly to n8n

**Decision:** All incoming leads pass through a FastAPI validation layer before anything else touches them.

**The problem with going directly to n8n:**  
n8n is an orchestration tool, not a backend. If you send a webhook directly to n8n, you get no input validation, no auth, no deduplication, and no persistence. If n8n is down or slow, the lead is silently lost. If someone sends malformed data, n8n either crashes the workflow or scores garbage.

**What FastAPI gives us:**  
- Pydantic models reject bad data at the boundary — before it enters the pipeline  
- API key auth blocks unsigned requests  
- The lead is persisted to PostgreSQL *before* n8n is notified — so even if n8n is down at that moment, the lead is safe and will be retried via Redis  
- A proper system of record exists independently of the orchestration layer

**Alternatives considered:**  
- Direct n8n webhook: rejected — no validation, no persistence guarantee  
- Flask: rejected — Pydantic integration is tighter in FastAPI; async support matters at 100+ leads/hour  
- Django: rejected — too heavyweight for a focused API service

---

## ADR-002 — Why Redis for the retry queue instead of a simple sleep/retry loop

**Decision:** Redis with exponential backoff and jitter handles all retries between FastAPI and n8n.

**The problem this solves:**  
At 100+ leads/hour hitting the same LLM API (OpenAI rate limits at ~3,500 RPM on standard tiers), a naive retry loop creates a thundering herd — every failed request retries at the same time, hammering the rate limit again. This was observed during testing: a burst of 20 leads would cause 20 simultaneous 429 errors, all retrying after the same 1-second sleep, all failing again.

**Why exponential backoff with jitter fixes this:**  
Jitter randomises the retry delay within a range. Instead of 20 requests all retrying at T+1s, they spread across T+0.8s, T+1.1s, T+1.6s, T+2.3s... The rate limit recovers, requests succeed, no data is lost.

**Why Redis specifically (not an in-memory queue):**  
If the FastAPI process restarts — which happens during deployments — an in-memory queue loses every pending retry. Redis persists the queue to disk. Leads survive process restarts.

**Alternatives considered:**  
- `time.sleep()` retry loop: rejected — thundering herd problem at scale, blocks the thread  
- Celery: considered — more powerful, but significantly more complex to operate. Redis alone covers this use case without the Celery worker overhead  
- n8n's built-in retry: rejected — by the time n8n has the lead, we've already lost the pre-persistence safety guarantee

---

## ADR-003 — Why PostgreSQL instead of SQLite or a spreadsheet

**Decision:** PostgreSQL is the system of record for all leads and scores.

**The problem this replaces:**  
The client (a 12-person SME sales team) was tracking leads in a Google Sheets spreadsheet. The problems were: no deduplication, no audit trail, race conditions when two reps edited simultaneously, and no way to query "all leads that scored 7+ this week" without manual filtering.

**Why PostgreSQL:**  
- ACID transactions prevent duplicate leads from being created even under concurrent load (handled via the `UNIQUE` constraint on email + a `409` response)  
- SQL query on `min_score` parameter gives the sales team filtered views the spreadsheet never could  
- SQLAlchemy ORM means schema changes don't require rewriting raw SQL across the codebase  
- Runs identically in Docker locally and on any cloud provider in production — no vendor lock-in

**Alternatives considered:**  
- SQLite: rejected — no concurrent write support; breaks under any real load  
- MongoDB: rejected — no strong consistency guarantees; the score write-back from n8n is a two-phase update that needs ACID  
- Airtable/Sheets as DB: rejected — not a database; no transactional guarantees

---

## ADR-004 — Why n8n for AI orchestration instead of calling the LLM directly from FastAPI

**Decision:** n8n handles the LLM call, prompt construction, and score write-back — FastAPI does not call the LLM directly.

**The reasoning:**  
Mixing orchestration logic into the FastAPI app creates a tightly coupled system that's hard to change. If the client wants to swap OpenAI for a local LLM, change the scoring prompt, add a human-review step for low-confidence scores, or add a Slack notification for 9+ scores — all of that is a drag-and-drop change in n8n, not a code deployment.

**What this separation gives us:**  
- FastAPI is stateless and fast — it validates, persists, and queues. That's it.  
- n8n owns the business logic of *how* a lead gets scored — prompt, model, thresholds, notifications  
- The two systems are decoupled: either can be updated, restarted, or replaced without touching the other

**Alternatives considered:**  
- LangChain directly in FastAPI: rejected for this use case — the client is non-technical; a visual n8n workflow they can inspect and modify themselves has higher adoption value than Python code they can't read  
- Make.com / Zapier: considered — n8n is self-hosted, meaning zero per-task pricing. At 100+ leads/hour, Make.com's task pricing becomes significant. n8n on Docker costs nothing to run.

---

## ADR-005 — Why the MCP endpoint was added

**Decision:** A `/mcp/score/{id}` endpoint exposes scored leads to downstream AI agents using the Model Context Protocol pattern.

**The problem this solves:**  
A standalone scoring system is a dead end if nothing can consume it. The MCP endpoint makes the lead scoring engine callable as a *tool* by any AI agent — a LangGraph orchestration workflow, a CrewAI agent, or any future multi-agent system — without custom integration code.

**What this enables:**  
- A downstream sales automation agent can call `query_lead_score(lead_id)` as a native tool  
- The scoring system becomes a composable service in a larger AI stack  
- This is the architecture pattern behind enterprise AI systems: small, focused services exposed as agent-callable tools

**This decision anticipates the next project:**  
The multi-agent business workflow (Project 2 in the portfolio) will call this endpoint directly. Building MCP exposure here, even when this project doesn't need it yet, demonstrates forward-looking architecture thinking.

---

## Failures encountered and resolved

| Failure | What happened | How it was fixed |
|---|---|---|
| Silent lead loss on n8n timeout | FastAPI waited forever if n8n didn't respond | Added `timeout=10` to all outbound requests |
| Duplicate scoring | Same lead submitted twice got scored twice, inflating sales pipeline | Added `UNIQUE` constraint on email + `409 Conflict` response |
| Redis connection leak | `redis.Redis()` created a new connection per request under load | Switched to `redis.from_url()` with a connection pool |
| Missing score callback | n8n had no API endpoint to write the score back to FastAPI | Added `PATCH /leads/{id}/score` endpoint |
| Only 429 errors caught | `ConnectionError` and `Timeout` during n8n outage silently dropped leads | Extended exception handling to cover `Timeout`, `ConnectionError`, `HTTPError` |
| No way to retrieve leads | POST created leads but there was no GET — unmonitorable | Added `GET /leads/{id}` and `GET /leads/?min_score=N` |

---

## What I would change at 10× scale

- Replace Redis queue with a proper message broker (RabbitMQ or Kafka) for guaranteed delivery and consumer groups  
- Add Prometheus metrics on the FastAPI app — currently observability is limited to logs  
- Move the n8n scoring prompt to a versioned prompt registry so prompt changes are auditable  
- Add a dead-letter queue for leads that fail scoring after max retries — currently they stay in `pending` status forever

---

## Security & Reliability Audit — June 2026

A full security audit was conducted after initial deployment. Findings and resolutions:

### Fixed
| Issue | Severity | Fix Applied |
|---|---|---|
| Score endpoint had no authentication | 🔴 Critical | Added `verify_n8n_key` dependency with separate `N8N_API_KEY` |
| Leads stuck in `pending` after max retries | 🟡 High | Added `mark_lead_failed()` — status now set to `scoring_failed` |
| Silent SQLite fallback if DATABASE_URL unset | 🟡 High | Replaced with explicit warning — no silent data loss |
| Dockerfile ran as root, no health check | 🟡 Medium | Multi-stage build, non-root user, HEALTHCHECK added |
| MCP endpoint had binary High/Low recommendation | 🟢 Low | Added Medium Priority tier (scores 4–6) |

### Acknowledged — Planned Improvements
| Issue | Priority | Plan |
|---|---|---|
| Rate limiting on all endpoints | Medium | Add `slowapi` in next sprint |
| Structured JSON logging | Medium | Add `python-json-logger` for Grafana/Loki compatibility |
| Alembic database migrations | Medium | Replace `create_all` with proper migration files |
| True Redis queue (not retry loop) | Low | Evaluate RQ or Celery when volume exceeds 500 leads/hour |
| Test suite using PostgreSQL not SQLite | Low | Add Docker Compose test service in CI |

### What was deliberately kept simple
The retry loop in `queue.py` is not a true message queue — it's intentional. For an SME use case at <100 leads/hour, a decoupled Celery/Redis worker adds operational complexity with no real benefit. The DECISIONS.md entry for ADR-002 documents this tradeoff explicitly. At 500+ leads/hour, this would be revisited.
