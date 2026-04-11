"""Quality filters for message preprocessing. Pure functions returning True to keep."""

import re

from personabot.schemas.discord import DiscordMessage

# Broad Unicode emoji ranges
_EMOJI_PATTERN = re.compile(
    r"^[\s"
    r"\U0001F600-\U0001F64F"  # Emoticons
    r"\U0001F300-\U0001F5FF"  # Misc Symbols and Pictographs
    r"\U0001F680-\U0001F6FF"  # Transport and Map
    r"\U0001F900-\U0001F9FF"  # Supplemental Symbols
    r"\U0001FA00-\U0001FA6F"  # Chess Symbols
    r"\U0001FA70-\U0001FAFF"  # Symbols and Pictographs Extended-A
    r"\u2600-\u26FF"  # Misc Symbols
    r"\u2700-\u27BF"  # Dingbats
    r"\uFE00-\uFE0F"  # Variation Selectors
    r"\u200D"  # Zero Width Joiner
    r"]+$"
)

_LOW_EFFORT = frozenset(
    {
        "lol",
        "lmao",
        "bruh",
        "rip",
        "f",
        "gg",
        "nice",
        "oof",
        "yep",
        "nope",
        "yea",
        "yeah",
        "nah",
        "ok",
        "okay",
        "sure",
        "same",
        "true",
        "bet",
        "fr",
        "smh",
        "tbh",
        "ngl",
        "idk",
        "omg",
        "wow",
        "damn",
        "dang",
        "yes",
        "no",
        "haha",
        "hahaha",
        "lmfao",
        "rofl",
        "xd",
    }
)

_URL_PATTERN = re.compile(r"https?://\S+")

_BOT_PREFIXES = ("!", "/", "$")


def is_long_enough(msg: DiscordMessage) -> bool:
    """Keep messages with 3+ words."""
    return msg.word_count >= 3


def is_not_emoji_only(msg: DiscordMessage) -> bool:
    """Discard messages that are only emoji and whitespace."""
    return not _EMOJI_PATTERN.match(msg.content)


def is_not_low_effort(msg: DiscordMessage) -> bool:
    """Discard common low-effort stock responses."""
    return msg.content.strip().lower() not in _LOW_EFFORT


def is_not_link_only(msg: DiscordMessage) -> bool:
    """Discard messages that are only URLs with no commentary."""
    if not _URL_PATTERN.search(msg.content):
        return True  # No URLs — keep without allocating a new string
    stripped = _URL_PATTERN.sub("", msg.content).strip()
    return len(stripped) > 0


def is_not_bot_command(msg: DiscordMessage) -> bool:
    """Discard messages that look like bot commands."""
    content = msg.content.strip()
    return not content.startswith(_BOT_PREFIXES) if content else True


def passes_all_filters(msg: DiscordMessage) -> bool:
    """Apply all quality filters in sequence. True = keep."""
    return (
        is_long_enough(msg)
        and is_not_emoji_only(msg)
        and is_not_low_effort(msg)
        and is_not_link_only(msg)
        and is_not_bot_command(msg)
    )
