"""ORM models package.

Importing the models here registers them on ``Base.metadata`` so that
metadata-based table creation and Alembic autogenerate see every table.
"""

from podex.models.base import Base
from podex.models.episode import Episode
from podex.models.podcast import Podcast

__all__ = ["Base", "Episode", "Podcast"]
