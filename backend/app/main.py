from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.openapi.utils import get_openapi
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text

from app.database import Base, engine

from app.models.user import User
from app.models.password_reset import PasswordResetToken
from app.models.project import Project
from app.models.project_file import ProjectFile
from app.models.run import Run
from app.models.run_result import RunResult
from app.models.test_result import TestResult
from app.models.step_result import StepResult
from app.models.screenshot import Screenshot
from app.models.failure_diagnostic import FailureDiagnostic
from app.models.schedule import Schedule

from app.routers.auth import router as auth_router
from app.routers.projects import router as projects_router
from app.routers.project_files import router as project_files_router
from app.routers.runs import router as runs_router
from app.routers.run_results import router as run_results_router
from app.routers.schedules import router as schedules_router
from app.services import scheduler_service


# ============================================================
# DATABASE
# ============================================================

Base.metadata.create_all(bind=engine)


@asynccontextmanager
async def lifespan(app: FastAPI):
    scheduler_service.start_scheduler()
    yield
    await scheduler_service.stop_scheduler()


# ============================================================
# FASTAPI APPLICATION
# ============================================================

app = FastAPI(
    title="Synthetic Monitor API",
    version="1.0.0",
    lifespan=lifespan,
)


# ============================================================
# CORS
# ============================================================

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
        "http://127.0.0.1:5173",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ============================================================
# ROUTERS
# ============================================================

app.include_router(auth_router)
app.include_router(projects_router)
app.include_router(project_files_router)
app.include_router(runs_router)
app.include_router(run_results_router)
app.include_router(schedules_router)


# ============================================================
# ROOT ENDPOINT
# ============================================================

@app.get("/")
def root():
    return {
        "message": "Synthetic Monitor API is running"
    }


# ============================================================
# HEALTH CHECK
# ============================================================

@app.get("/api/health")
def health():
    try:
        with engine.connect() as connection:
            result = connection.execute(
                text("SELECT 1 AS database_status")
            )

            row = result.fetchone()

        return {
            "status": "healthy",
            "database": "connected",
            "database_status": row[0],
        }

    except Exception as exc:
        return {
            "status": "unhealthy",
            "database": "disconnected",
            "error": str(exc),
        }



def custom_openapi():
    if app.openapi_schema:
        return app.openapi_schema

    openapi_schema = get_openapi(
        title=app.title,
        version=app.version,
        description=app.description,
        routes=app.routes,
    )

    schema_name = (
        "Body_upload_project_files_api_projects__project_id__files_post"
    )

    schema = (
        openapi_schema
        .get("components", {})
        .get("schemas", {})
        .get(schema_name)
    )

    if schema:
        files_schema = (
            schema
            .get("properties", {})
            .get("files")
        )

        if files_schema:
            items = files_schema.get("items")

            if items and items.get("contentMediaType"):
                items.pop("contentMediaType", None)
                items["format"] = "binary"

    app.openapi_schema = openapi_schema

    return app.openapi_schema


app.openapi = custom_openapi
