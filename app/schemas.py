from pydantic import BaseModel, EmailStr, Field
from typing import Optional
from datetime import datetime

class LeadCreate(BaseModel):
    first_name: str
    last_name: str
    email: EmailStr
    company: str
    job_title: str
    annual_revenue: Optional[float] = 0
    employee_count: Optional[int] = 0
    industry: str
    message: Optional[str] = None

class Lead(LeadCreate):
    id: int
    score: int = 0
    status: str = "pending"
    created_at: datetime

    class Config:
        from_attributes = True
