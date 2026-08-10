"""Declarative base for all ORM models.

Centralizes the SQLAlchemy Base class so that all models
import from one place instead of creating circular dependencies.
"""
from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    """Declarative base for all ORM models."""
    pass
