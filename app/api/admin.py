from fastapi import APIRouter, Depends, Header, HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from app.config import settings
from app.database.models import JobPost, ScanRun
from app.database.session import get_session

router = APIRouter(prefix="/api")

def admin(authorization: str = Header(default="")):
    token = settings().admin_api_token
    if not token or authorization != f"Bearer {token}":
        raise HTTPException(401, "管理员令牌无效")

@router.get("/stats/today", dependencies=[Depends(admin)])
def stats(session: Session = Depends(get_session)):
    return {"runs": session.scalar(select(func.count()).select_from(ScanRun)), "jobs": session.scalar(select(func.count()).select_from(JobPost))}

@router.get("/jobs", dependencies=[Depends(admin)])
def jobs(session: Session = Depends(get_session)):
    return list(session.scalars(select(JobPost).order_by(JobPost.created_at.desc()).limit(100)))
