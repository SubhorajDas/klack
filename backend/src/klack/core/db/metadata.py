"""Central model import registry used by Alembic autogeneration.

Future feature model modules must be imported here before `target_metadata` is evaluated.
"""

import klack.modules.channels.infrastructure.models
import klack.modules.identity.infrastructure.models
import klack.modules.workspaces.infrastructure.models  # noqa: F401
from klack.core.db.base import Base

target_metadata = Base.metadata
