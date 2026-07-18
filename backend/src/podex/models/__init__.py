"""ORM models package.

Importing the models here registers them on ``Base.metadata`` so that
metadata-based table creation and Alembic autogenerate see every table.
"""

from podex.models.base import Base
from podex.models.episode import DiscoverySource, Episode
from podex.models.ingestion_run import IngestionRun, IngestionRunStatus
from podex.models.media import Media, MediaType
from podex.models.mention import Mention
from podex.models.podcast import Podcast

__all__ = [
    "Base",
    "DiscoverySource",
    "Episode",
    "IngestionRun",
    "IngestionRunStatus",
    "Media",
    "MediaType",
    "Mention",
    "Podcast",
]
