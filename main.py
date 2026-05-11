"""
Entry shim so `uvicorn main:app --reload` keeps working.
"""

from app.main import app

__all__ = ["app"]
