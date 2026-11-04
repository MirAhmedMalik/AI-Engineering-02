import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import app.config  # noqa: E402
from app.config import Settings  # noqa: E402
from app.cube.solver import OptimalSolver  # noqa: E402
from app.store import forget_stores  # noqa: E402


@pytest.fixture(scope="session")
def solver():
    s = OptimalSolver()
    _ = s.table
    return s


@pytest.fixture(autouse=True)
def _fresh_stores():
    forget_stores()
    yield
    forget_stores()


@pytest.fixture
def sim_settings(tmp_path):
    """Practice (simulated) models with the REAL teacher limits, no waiting, everything in a temp folder."""
    return Settings(
        mock_latency_scale=0.0,
        retry_backoff_s=0.0,
        results_dir=tmp_path / "results",
        data_dir=tmp_path / "data",
        student_name="Test Student",
        student_roll_no="TEST-001",
    )


@pytest.fixture
def big_settings(tmp_path, monkeypatch):
    """Like sim_settings but with huge request limits, for tests that play many races."""
    monkeypatch.setattr(app.config, "HARD_TOTAL_CEILING", 1_000_000)
    return Settings(
        mock_latency_scale=0.0,
        retry_backoff_s=0.0,
        results_dir=tmp_path / "results",
        data_dir=tmp_path / "data",
        max_claude_requests=100000,
        max_requests_per_decision_model=100000,
        max_total_requests=1_000_000,
        student_name="Test Student",
        student_roll_no="TEST-001",
    )
