

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

from app.core.security import create_access_token, hash_password
from app.database import engine, get_db
from app.main import app
from app.models.project import Project
from app.models.project_file import ProjectFile
from app.models.run import Run
from app.models.user import User


# ---------------------------------------------------------------------------
# Plain helper functions (imported directly by test modules, not injected
# as fixtures) — kept outside the fixture system because tests sometimes
# need to create several projects/files/runs per test with different
# db_session-bound objects explicitly passed in.
# ---------------------------------------------------------------------------

def make_project_file(db_session, project: Project, file_name: str, content: str = "") -> ProjectFile:
    project_file = ProjectFile(
        project_id=project.id,
        file_name=file_name,
        file_path=f"projects/{project.id}/{file_name}",
        file_content=content,
    )
    db_session.add(project_file)
    db_session.commit()
    db_session.refresh(project_file)
    return project_file


def make_run(db_session, project: Project, status: str = "QUEUED", **overrides) -> Run:
    run = Run(
        project_id=project.id,
        status=status,
        browser=overrides.get("browser", project.browser),
        cancel_requested=False,
        started_at=overrides.get("started_at"),
        completed_at=overrides.get("completed_at"),
    )
    db_session.add(run)
    db_session.commit()
    db_session.refresh(run)
    return run


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def db_session():
    connection = engine.connect()
    outer_transaction = connection.begin()

    TestSessionLocal = sessionmaker(
        bind=connection,
        autoflush=False,
        autocommit=False,
        join_transaction_mode="create_savepoint",
    )
    session = TestSessionLocal()

    try:
        yield session
    finally:
        session.close()
        outer_transaction.rollback()
        connection.close()


@pytest.fixture()
def client(db_session):
    def _override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = _override_get_db
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.pop(get_db, None)


@pytest.fixture()
def test_user(db_session) -> User:
    user = User(
        full_name="Test User",
        email="phase5-test-user@example.com",
        mobile_number="9000000001",
        password_hash=hash_password("Password123!"),
        is_active=True,
    )
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)
    return user


@pytest.fixture()
def test_project(db_session, test_user) -> Project:
    project = Project(
        name="Test Project",
        frequency="manual",
        browser="chrome",
        owner_id=test_user.id,
        is_active=True,
    )
    db_session.add(project)
    db_session.commit()
    db_session.refresh(project)
    return project


@pytest.fixture()
def auth_headers(test_user) -> dict:
    token = create_access_token(test_user.id)
    return {"Authorization": f"Bearer {token}"}
