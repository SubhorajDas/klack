"""Static Alembic repository invariants."""

from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory


def test_migration_history_has_at_most_one_head() -> None:
    backend_root = Path(__file__).parents[2]
    config = Config(backend_root / "alembic.ini")
    script = ScriptDirectory.from_config(config)

    assert len(script.get_heads()) <= 1
