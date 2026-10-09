import os

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, DeclarativeBase

# Docker/Linux cannot use Windows trusted auth; set DATABASE_URL there.
DATABASE_URL = os.environ.get("DATABASE_URL") or (
    "mssql+pyodbc://@DESKTOP-2I11G5V/Convergence"
    "?driver=ODBC+Driver+18+for+SQL+Server"
    "&trusted_connection=yes"
    "&TrustServerCertificate=yes"
)

engine = create_engine(
    DATABASE_URL,
    echo=True,
    # The SQL echo must not log statement parameters: they include the full
    # source of uploaded test scripts and recorded step/failure text, which can
    # contain credentials. Statements are still logged; values are not.
    hide_parameters=True,
)

SessionLocal = sessionmaker(
    bind=engine,
    autoflush=False,
    autocommit=False,
)


class Base(DeclarativeBase):
    pass


def get_db():
    db = SessionLocal()

    try:
        yield db
    finally:
        db.close()