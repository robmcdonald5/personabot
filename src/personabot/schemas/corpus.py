"""Corpus and conversation window schemas for the preprocessing pipeline."""

from pydantic import BaseModel, Field


class WindowMessage(BaseModel):
    """A message within a conversation window, formatted for export."""

    message_id: int
    content: str
    timestamp: str
    author_name: str = ""
    is_target_user: bool = True
    reaction_count: int = 0
    is_reply: bool = False
    reply_to_id: int | None = None
    reply_context: str | None = None
    score: float = 0.0


class ConversationWindow(BaseModel):
    """A group of temporally-related messages forming a conversation."""

    window_id: str
    channel_name: str
    channel_id: int
    start_time: str
    end_time: str
    participants: list[str] = Field(default_factory=list)
    messages: list[WindowMessage] = Field(default_factory=list)
    quality_score: float = 0.0


class DateRange(BaseModel):
    """Date range for corpus metadata."""

    earliest: str
    latest: str


class UserCorpus(BaseModel):
    """Complete exported corpus for a single user."""

    user_id: int
    user_name: str
    guild_id: int
    guild_name: str
    export_timestamp: str
    total_messages: int
    total_windows: int
    date_range: DateRange
    windows: list[ConversationWindow] = Field(default_factory=list)
