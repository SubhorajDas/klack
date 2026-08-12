"""Central model import registry used by Alembic autogeneration.

Future feature model modules must be imported here before `target_metadata` is evaluated.
"""

from klack.core.db.base import Base

target_metadata = Base.metadata
