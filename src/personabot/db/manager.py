"""Database connection lifecycle management."""

from pathlib import Path

import aiosqlite

from personabot.db.models import create_tables


class DatabaseManager:
    """Manages the SQLite database connection lifecycle."""

    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        self.db: aiosqlite.Connection | None = None

    async def connect(self) -> aiosqlite.Connection:
        """Open the database connection, create tables, and enable WAL mode."""
        self.db_path.parent.mkdir(parents=True, exist_ok=True)

        db = await aiosqlite.connect(str(self.db_path))
        db.row_factory = aiosqlite.Row
        await db.execute("PRAGMA journal_mode=WAL")
        await db.execute("PRAGMA foreign_keys = ON")
        await create_tables(db)
        self.db = db
        return db

    async def close(self) -> None:
        """Commit and close the database connection."""
        if self.db:
            await self.db.commit()
            await self.db.close()
            self.db = None

    def get_connection(self) -> aiosqlite.Connection:
        """Return the active connection, raising if not connected."""
        if self.db is None:
            raise RuntimeError("Database not connected. Call connect() first.")
        return self.db
