from pydantic import BaseModel, EmailStr, Field
from typing import Optional
from datetime import datetime


class LeadCreate(BaseModel):
    first_name: str = Field(..., min_length=1)
    last_name: str = Field(..., min_length=1)
    email: EmailStr
    company: str = Field(..., min_length=1)
    job_title: str = Field(..., min_length=1)
    annual_revenue: Optional[float] = Field(default=0, ge=0)
    employee_count: Optional[int] = Field(default=0, ge=0)
    industry: str = Field(..., min_length=1)
    message: Optional[str] = None


class Lead(LeadCreate):
    id: int
    score: int = 0
    status: str = "pending"
    created_at: datetime

    class Config:
        from_attributes = True
