"""Tests for PersonaBot client instantiation."""

from unittest.mock import MagicMock

from personabot.bot.client import PersonaBot
from personabot.config import Settings
from personabot.db.manager import DatabaseManager


def test_bot_instantiation():
    """Bot can be instantiated with required dependencies."""
    db_manager = MagicMock(spec=DatabaseManager)
    settings = Settings(discord_token="fake")

    bot = PersonaBot(db_manager=db_manager, settings=settings)

    assert bot.db_manager is db_manager
    assert bot.settings is settings


def test_bot_intents():
    """Bot declares the correct intents."""
    db_manager = MagicMock(spec=DatabaseManager)
    settings = Settings(discord_token="fake")

    bot = PersonaBot(db_manager=db_manager, settings=settings)

    assert bot.intents.message_content is True
    assert bot.intents.members is True


def test_bot_has_pb_group():
    """Bot has the /pb command group registered."""
    db_manager = MagicMock(spec=DatabaseManager)
    settings = Settings(discord_token="fake")

    bot = PersonaBot(db_manager=db_manager, settings=settings)

    assert bot.pb is not None
    assert bot.pb.name == "pb"
