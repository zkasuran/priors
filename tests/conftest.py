import pytest

from priors.memory import Memory


@pytest.fixture
def mem(tmp_path):
    return Memory(str(tmp_path / "memory.db"))
