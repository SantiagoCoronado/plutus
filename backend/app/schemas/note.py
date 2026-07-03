from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

MAX_NOTE_BYTES = 100_000


class NoteCreate(BaseModel):
    title: str | None = Field(default=None, max_length=300)
    body_md: str = Field(min_length=1, max_length=MAX_NOTE_BYTES)


class NoteUpdate(BaseModel):
    title: str | None = Field(default=None, max_length=300)
    body_md: str | None = Field(default=None, min_length=1, max_length=MAX_NOTE_BYTES)


class NoteOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    asset_id: int
    title: str | None
    body_md: str
    source: str
    created_at: datetime
    updated_at: datetime
