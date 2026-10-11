"""Application entry point.  Run with:  uvicorn app.main:app --reload"""

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.config import settings
from app.database import init_db
from app.routers import jobs

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


@asynccontextmanager
async def lifespan(_app: FastAPI):
    init_db()
    settings.storage_dir.mkdir(parents=True, exist_ok=True)
    yield


app = FastAPI(
    title="Bulk Certificate Generator",
    description="Submit a list of recipients, get certificates generated in the background, "
    "track progress and download the PDFs.",
    version="1.0.0",
    lifespan=lifespan,
)
app.include_router(jobs.router)


@app.get("/health", tags=["meta"])
def health():
    return {"status": "ok"}
