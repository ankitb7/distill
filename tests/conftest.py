import tempfile
from pathlib import Path

import pytest

from ai_pulse.db import Database


@pytest.fixture
def tmp_db():
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db = Database(Path(f.name))
        db.init_schema()
        yield db
        db.close()
