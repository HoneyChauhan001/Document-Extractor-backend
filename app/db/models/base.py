"""
app/db/models/base.py
──────────────────────
Shared SQLAlchemy declarative base.

ALL ORM model classes must inherit from `Base` defined here.
Keeping Base in its own file avoids circular imports:
  session.py  →  models/*.py  →  base.py   (no cycle)
"""

from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    """
    Empty base class — SQLAlchemy uses it to register all subclassing models
    in its metadata registry.  `Base.metadata` is passed to `create_all()`
    or Alembic's migration env when generating DDL.
    """
    pass
