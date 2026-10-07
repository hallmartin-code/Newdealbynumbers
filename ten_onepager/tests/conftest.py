import copy
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from onepager.demo import load_demo_analysis  # noqa: E402
from onepager.models import Analysis  # noqa: E402


@pytest.fixture
def demo() -> Analysis:
    return load_demo_analysis()


@pytest.fixture
def demo_dict(demo) -> dict:
    return copy.deepcopy(demo.model_dump(mode="json"))


@pytest.fixture
def samples_dir() -> Path:
    return ROOT / "samples"


def make(d: dict) -> Analysis:
    return Analysis.model_validate(d)


def metric(section: dict, mid: str) -> dict:
    return next(m for m in section["metrics"] if m["id"] == mid)


def dump(obj) -> str:
    return json.dumps(obj)
