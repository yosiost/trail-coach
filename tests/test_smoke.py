"""Import-smoke: the full app wires up. Skipped where the LLM stack isn't installed."""
import os
import pytest


def test_full_app_imports():
    pytest.importorskip("litellm")   # skip locally (3.9 / no litellm); runs in CI
    os.environ["FLASK_DEBUG"] = "1"  # so the prod SECRET_KEY guard doesn't raise
    import server
    assert server.app is not None
