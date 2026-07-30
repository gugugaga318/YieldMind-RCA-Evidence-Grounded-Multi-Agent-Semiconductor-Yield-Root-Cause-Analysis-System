"""FastAPI adapter for the Semiconductor Yield RCA core workflow."""

from __future__ import annotations

from yield_rca_api.app import create_app

__all__ = ["create_app", "__version__"]

__version__ = "0.1.0"
