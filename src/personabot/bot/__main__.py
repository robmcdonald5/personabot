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
    # Dev mode rebuilds the schema on every startup; prod leaves it to dbmate.
    db_manager = DatabaseManager(
        database_url=settings.database_url,
        reset_schema_on_connect=settings.is_development,
    )
    bot = PersonaBot(db_manager=db_manager, settings=settings)
    bot.run(settings.discord_token)


if __name__ == "__main__":
    main()
