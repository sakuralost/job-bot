from contextlib import asynccontextmanager
from fastapi import FastAPI
from app.api import admin, health
from app.config import settings
from app.dashboard import router as dashboard_router
from app.scheduler.service import build_scheduler
from app.services.scan_schedule import enqueue_due_scan
from worker.manual_scan import resume_active_manual_scan

@asynccontextmanager
async def lifespan(_: FastAPI):
    scheduler = build_scheduler(enqueue_due_scan)
    scheduler.start()
    resume_active_manual_scan()
    yield
    scheduler.shutdown(wait=False)

app = FastAPI(title=settings().app_name, lifespan=lifespan)
app.include_router(health.router)
app.include_router(admin.router)
app.include_router(dashboard_router)
