from pathlib import Path

import pytest

from cms_reporter.config import Producer, Settings, Vertical, load_config

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(autouse=True)
def master_key(monkeypatch):
    monkeypatch.setenv("CMS_MASTER_KEY", "unit-test-master-key-0123456789abcdef")


@pytest.fixture
def cfg():
    return load_config(ROOT / "config" / "network.yaml")


@pytest.fixture
def settings(tmp_path):
    return Settings(data_dir=tmp_path / "data", config_path=ROOT / "config" / "network.yaml")


@pytest.fixture
def vertical():
    return Vertical(
        "music",
        "Music",
        "pool",
        (
            Producer("Alpha", 0.10, None, ("alpha", "alp_"), ("Alpha Label",), ("UC_A",)),
            Producer("Beta", 0.20, None, ("beta",), ("Beta Label", "Shared Imprint"), ("UC_B",)),
        ),
    )
