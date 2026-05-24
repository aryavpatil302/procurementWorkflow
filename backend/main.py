import os

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles

from backend.database import init_db
from backend.routers import approvals as approvals_router
from backend.routers import requests as requests_router
from backend.routers import questionnaire as questionnaire_router
from backend.routers import analytics as analytics_router
from backend.routers import workflow as workflow_router

_FRONTEND_DIR = os.path.join(os.path.dirname(__file__), "..", "frontend")

app = FastAPI(title="Procurement Intake Agent", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
def on_startup():
    if not os.getenv("GROQ_API_KEY"):
        raise EnvironmentError(
            "GROQ_API_KEY environment variable is not set. "
            "Add it to your .env file before starting the server."
        )
    init_db()


app.include_router(requests_router.router)
app.include_router(questionnaire_router.router)
app.include_router(approvals_router.router)
app.include_router(workflow_router.router)
app.include_router(analytics_router.router)


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/")
def root():
    return RedirectResponse(url="/dashboard.html")


# Serve frontend static files — mounted last so API routes take precedence
app.mount("/", StaticFiles(directory=_FRONTEND_DIR), name="frontend")
