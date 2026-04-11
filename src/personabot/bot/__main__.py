"""Entry point: python -m personabot.bot"""

import logging

from personabot.bot.client import PersonaBot
from personabot.config import get_settings
from personabot.db.manager import DatabaseManager


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
    )

    settings = get_settings()
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    db_manager = DatabaseManager(db_path=settings.db_path)
    bot = PersonaBot(db_manager=db_manager, settings=settings)
    bot.run(settings.discord_token)


if __name__ == "__main__":
    main()
