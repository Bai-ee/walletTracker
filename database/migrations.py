"""Database schema setup and upgrade utilities."""
from database.db import engine, init_db
from database.models import Base


def setup_database():
    """Initialize the database schema. Safe to call multiple times."""
    init_db()
    print("Database schema created/verified successfully.")


def reset_database():
    """Drop all tables and recreate. USE WITH CAUTION."""
    Base.metadata.drop_all(bind=engine)
    init_db()
    print("Database reset successfully.")


if __name__ == "__main__":
    setup_database()
