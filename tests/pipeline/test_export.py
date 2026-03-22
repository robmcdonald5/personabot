"""Tests for JSONL export and token budget enforcement."""

import json

from personabot.pipeline.export import (
    build_user_corpus,
    enforce_token_budget,
    estimate_tokens,
    export_jsonl,
)
from personabot.schemas.corpus import ConversationWindow, WindowMessage


def _window(
    window_id: str = "w_001",
    quality_score: float = 0.5,
    content: str = "Test message content",
) -> ConversationWindow:
    return ConversationWindow(
        window_id=window_id,
        channel_name="general",
        channel_id=100,
        start_time="2026-01-01T12:00:00Z",
        end_time="2026-01-01T12:30:00Z",
        participants=["test"],
        messages=[
            WindowMessage(
                message_id=1,
                content=content,
                timestamp="2026-01-01T12:00:00Z",
                is_target_user=True,
                score=quality_score,
            )
        ],
        quality_score=quality_score,
    )


def test_estimate_tokens():
    assert estimate_tokens("") == 0
    assert estimate_tokens("a" * 400) == 100


def test_enforce_token_budget_includes_within_budget():
    windows = [_window(f"w_{i:03d}", quality_score=0.5) for i in range(3)]
    selected = enforce_token_budget(windows, total_budget=100000)
    assert len(selected) == 3


def test_enforce_token_budget_respects_limit():
    # Create windows with large content to exceed budget
    big_content = "word " * 5000  # ~25000 chars = ~6250 tokens + overhead
    windows = [
        _window(f"w_{i:03d}", quality_score=0.5 + i * 0.1, content=big_content)
        for i in range(20)
    ]
    selected = enforce_token_budget(windows, total_budget=50000)
    assert len(selected) < 20


def test_enforce_token_budget_prioritizes_quality():
    windows = [
        _window("w_001", quality_score=0.9, content="word " * 5000),
        _window("w_002", quality_score=0.1, content="word " * 5000),
    ]
    # Tiny budget: only room for one
    selected = enforce_token_budget(windows, total_budget=10000)
    assert len(selected) == 1
    assert selected[0].window_id == "w_001"


def test_build_user_corpus():
    windows = [_window("w_001"), _window("w_002")]
    corpus = build_user_corpus(
        user_id=300,
        user_name="testuser",
        guild_id=100,
        guild_name="Test Server",
        windows=windows,
    )
    assert corpus.total_messages == 2
    assert corpus.total_windows == 2
    assert corpus.user_name == "testuser"
    assert corpus.date_range.earliest != ""


def test_export_jsonl(tmp_path):
    windows = [_window("w_001"), _window("w_002")]
    corpus = build_user_corpus(300, "test", 100, "Server", windows)
    out = export_jsonl(corpus, str(tmp_path / "corpus.jsonl"))
    assert out.exists()
    with open(out) as f:
        lines = f.readlines()
    # Line 1: metadata, Lines 2-3: one window each
    assert len(lines) == 3
    metadata = json.loads(lines[0])
    assert metadata["user_name"] == "test"
    assert "windows" not in metadata
    window_1 = json.loads(lines[1])
    assert window_1["window_id"] == "w_001"
