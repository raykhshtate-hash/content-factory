"""Shared pytest fixtures for the test suite.

Introduced in Phase 7 Plan 07-01 to expose a `fixtures_dir` path for
binary test assets (sample.jpg / sample.heic for carousel renderer tests).
"""
from pathlib import Path

import pytest


@pytest.fixture
def fixtures_dir() -> Path:
    """Directory containing binary test fixtures (images, etc.)."""
    return Path(__file__).parent / "fixtures"
