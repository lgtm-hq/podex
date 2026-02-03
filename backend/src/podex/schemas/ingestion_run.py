"""Pydantic schemas for ingestion runs."""

from datetime import datetime

from pydantic import BaseModel, ConfigDict


class IngestionRunBase(BaseModel):
    """Base ingestion run schema."""

    status: str
    error_summary: str | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None


class IngestionRunCreate(IngestionRunBase):
    """Schema for creating ingestion runs."""

    status: str


class IngestionRunResponse(IngestionRunBase):
    """Schema for ingestion run responses."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    created_at: datetime
