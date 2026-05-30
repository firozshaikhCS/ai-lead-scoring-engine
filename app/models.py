from sqlalchemy import Column, Integer, String, Float, DateTime, Text
from sqlalchemy.sql import func
from .database import Base

class LeadDB(Base):
    __tablename__ = "leads"
    id = Column(Integer, primary_key=True, index=True)
    first_name = Column(String)
    last_name = Column(String)
    email = Column(String, index=True)
    company = Column(String)
    job_title = Column(String)
    annual_revenue = Column(Float, default=0)
    employee_count = Column(Integer, default=0)
    industry = Column(String)
    message = Column(Text)
    score = Column(Integer, default=0)
    status = Column(String, default="pending")
    created_at = Column(DateTime(timezone=True), server_default=func.now())
