import os

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from backend.database import init_db
from backend.routers import requests as requests_router
from backend.routers import questionnaire as questionnaire_router

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


@app.get("/health")
def health():
    return {"status": "ok"}
