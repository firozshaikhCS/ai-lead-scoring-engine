from fastapi import FastAPI, Depends, BackgroundTasks, HTTPException, Security
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from sqlalchemy.orm import Session
from . import models, schemas, database
from .queue import send_to_n8n_with_retry
import os
import logging

logger = logging.getLogger(__name__)

# GAP FIX: Original guide mentioned JWT but never implemented it.
# Using simple API key auth here — swap for JWT (python-jose) in production.
API_KEY = os.getenv("API_KEY", "dev-key-change-in-production")
security = HTTPBearer()

def verify_api_key(credentials: HTTPAuthorizationCredentials = Security(security)):
    if credentials.credentials != API_KEY:
        raise HTTPException(status_code=401, detail="Invalid API key")
    return credentials.credentials

# Create tables on startup
models.Base.metadata.create_all(bind=database.engine)

app = FastAPI(
    title="AI Lead Scoring Engine",
    description="Scores sales leads 1–10 using AI. Built by Firoz Shaikh.",
    version="1.0.0"
)

@app.get("/health")
def health_check():
    """GAP FIX: Original had no health endpoint — essential for any deployed service."""
    return {"status": "ok"}

@app.post("/leads/", response_model=schemas.Lead)
def create_lead(
    lead: schemas.LeadCreate,
    background_tasks: BackgroundTasks,
    db: Session = Depends(database.get_db),
    api_key: str = Depends(verify_api_key)
):
    """
    Receive a lead, persist it, and queue it for AI scoring.
    The score is written back asynchronously by n8n.
    """
    # Check for duplicate email — GAP FIX: original had no deduplication
    existing = db.query(models.LeadDB).filter(
        models.LeadDB.email == lead.email
    ).first()
    if existing:
        raise HTTPException(
            status_code=409,
            detail=f"Lead with email {lead.email} already exists (id: {existing.id})"
        )

    db_lead = models.LeadDB(**lead.model_dump())
    db.add(db_lead)
    db.commit()
    db.refresh(db_lead)

    # Queue for async AI scoring — does NOT block the API response
    background_tasks.add_task(send_to_n8n_with_retry, db_lead.id)

    logger.info(f"Lead {db_lead.id} created and queued for scoring")
    return db_lead


@app.get("/leads/{lead_id}", response_model=schemas.Lead)
def get_lead(
    lead_id: int,
    db: Session = Depends(database.get_db),
    api_key: str = Depends(verify_api_key)
):
    """GAP FIX: Original guide had no GET endpoint — you need to retrieve leads."""
    lead = db.query(models.LeadDB).filter(models.LeadDB.id == lead_id).first()
    if not lead:
        raise HTTPException(status_code=404, detail="Lead not found")
    return lead


@app.patch("/leads/{lead_id}/score")
def update_score(
    lead_id: int,
    score: int,
    db: Session = Depends(database.get_db)
):
    """
    Called by n8n after AI scoring completes.
    GAP FIX: Original guide updated score directly in n8n PostgreSQL node
    but had no API endpoint for n8n to call back — this is the missing piece.
    """
    if not 1 <= score <= 10:
        raise HTTPException(status_code=422, detail="Score must be between 1 and 10")

    lead = db.query(models.LeadDB).filter(models.LeadDB.id == lead_id).first()
    if not lead:
        raise HTTPException(status_code=404, detail="Lead not found")

    lead.score = score
    lead.status = "scored"
    db.commit()
    return {"lead_id": lead_id, "score": score, "status": "scored"}


@app.get("/mcp/score/{lead_id}")
def get_mcp_score(
    lead_id: int,
    db: Session = Depends(database.get_db),
    api_key: str = Depends(verify_api_key)
):
    """
    MCP endpoint — exposes scored lead data to downstream AI agents.
    Returns structured recommendation for agent consumption.
    """
    lead = db.query(models.LeadDB).filter(models.LeadDB.id == lead_id).first()
    if not lead:
        raise HTTPException(status_code=404, detail="Lead not found")

    if lead.status == "pending":
        return {
            "lead_id": lead_id,
            "status": "pending",
            "message": "Scoring in progress — retry in a few seconds"
        }

    return {
        "lead_id": lead_id,
        "score": lead.score,
        "status": lead.status,
        "recommendation": "High Priority" if lead.score >= 7 else "Low Priority",
        "company": lead.company,
        "job_title": lead.job_title,
    }


@app.get("/leads/")
def list_leads(
    min_score: int = 0,
    status: str = None,
    db: Session = Depends(database.get_db),
    api_key: str = Depends(verify_api_key)
):
    """
    GAP FIX: Original had no list/filter endpoint.
    This is the core value — filter leads scoring 7+ for reps.
    """
    query = db.query(models.LeadDB)
    if min_score > 0:
        query = query.filter(models.LeadDB.score >= min_score)
    if status:
        query = query.filter(models.LeadDB.status == status)
    return query.order_by(models.LeadDB.score.desc()).all()
