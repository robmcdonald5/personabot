"""JSONL export and token budget enforcement."""

import json
from datetime import datetime, timezone
from pathlib import Path

from personabot.schemas.corpus import ConversationWindow, DateRange, UserCorpus

_WINDOW_METADATA_OVERHEAD = 50


def estimate_tokens(text: str) -> int:
    """Rough token estimate: ~4 characters per token."""
    return len(text) // 4


def _window_token_cost(window: ConversationWindow) -> int:
    """Estimate the token cost of a single window."""
    joined = " ".join(m.content for m in window.messages)
    return estimate_tokens(joined) + _WINDOW_METADATA_OVERHEAD


def enforce_token_budget(
    windows: list[ConversationWindow],
    total_budget: int = 100000,
    corpus_ratio: float = 0.80,
) -> list[ConversationWindow]:
    """Select windows within token budget, prioritizing by quality_score.

    Windows are included whole (never split) in quality-descending order.
    """
    corpus_budget = int(total_budget * corpus_ratio)
    sorted_windows = sorted(windows, key=lambda w: w.quality_score, reverse=True)

    selected: list[ConversationWindow] = []
    tokens_used = 0

    for window in sorted_windows:
        cost = _window_token_cost(window)
        if tokens_used + cost <= corpus_budget:
            selected.append(window)
            tokens_used += cost

    # Re-sort selected by start_time for chronological output
    selected.sort(key=lambda w: w.start_time)
    return selected


def build_user_corpus(
    user_id: int,
    user_name: str,
    guild_id: int,
    guild_name: str,
    windows: list[ConversationWindow],
) -> UserCorpus:
    """Assemble a UserCorpus from selected windows."""
    total_messages = sum(len(w.messages) for w in windows)

    # Windows are sorted chronologically; use their boundary timestamps
    if windows:
        earliest = windows[0].start_time
        latest = windows[-1].end_time
    else:
        earliest = ""
        latest = ""

    return UserCorpus(
        user_id=user_id,
        user_name=user_name,
        guild_id=guild_id,
        guild_name=guild_name,
        export_timestamp=datetime.now(timezone.utc).isoformat(),
        total_messages=total_messages,
        total_windows=len(windows),
        date_range=DateRange(earliest=earliest, latest=latest),
        windows=windows,
    )


def export_jsonl(corpus: UserCorpus, output_path: Path) -> Path:
    """Write corpus to a JSONL file (one JSON object per line).

    Line 1: corpus metadata (without windows).
    Lines 2+: one ConversationWindow per line.
    """
    output_path.parent.mkdir(parents=True, exist_ok=True)

    with open(output_path, "w", encoding="utf-8") as f:
        # Line 1: metadata without windows
        metadata = corpus.model_dump(exclude={"windows"})
        f.write(json.dumps(metadata, default=str) + "\n")
        # Lines 2+: one window per line
        for window in corpus.windows:
            f.write(window.model_dump_json() + "\n")

    return output_path
