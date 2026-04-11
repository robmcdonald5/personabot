"""Discord message schemas for storage and scoring."""

from enum import StrEnum, auto

from pydantic import BaseModel, Field


class JobStatus(StrEnum):
    """Scrape job lifecycle states."""

    PENDING = auto()
    RUNNING = auto()
    COMPLETED = auto()
    FAILED = auto()
    CANCELLED = auto()


TERMINAL_STATUSES = frozenset(
    {JobStatus.COMPLETED, JobStatus.FAILED, JobStatus.CANCELLED}
)


class DiscordMessage(BaseModel):
    """A Discord message with full metadata from the database."""

    message_id: int
    guild_id: int
    channel_id: int
    channel_name: str = ""
    author_id: int
    author_name: str
    content: str
    timestamp: str
    reaction_count: int = 0
    reply_to_id: int | None = None
    thread_id: int | None = None
    is_pinned: bool = False
    attachment_count: int = 0
    embed_count: int = 0
    word_count: int = 0


class ScoredMessage(DiscordMessage):
    """A Discord message with quality scoring applied."""

    score: float = Field(default=0.0, ge=0.0)
    score_breakdown: dict[str, float] = Field(default_factory=dict)
