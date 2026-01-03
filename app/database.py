import os
from pathlib import Path
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base
from fastapi.templating import Jinja2Templates

# Get database path from environment or use default
DATABASE_PATH = os.getenv("DATABASE_PATH", "foreclosures.db")

# Support both absolute and relative paths
if not os.path.isabs(DATABASE_PATH):
    # If relative path, make it relative to project root
    BASE_DIR = Path(__file__).resolve().parent.parent
    DATABASE_PATH = str(BASE_DIR / DATABASE_PATH)

SQLALCHEMY_DATABASE_URL = f"sqlite:///{DATABASE_PATH}"
engine = create_engine(
    SQLALCHEMY_DATABASE_URL, 
    connect_args={"check_same_thread": False}
)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

# Templates configuration
TEMPLATE_DIR = Path(__file__).resolve().parent / "templates"
templates = Jinja2Templates(directory=str(TEMPLATE_DIR))

# Dependency for FastAPI routes
def get_db():
    """Database session dependency for FastAPI"""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
