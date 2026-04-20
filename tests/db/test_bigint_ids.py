"""Structural regression guard against Postgres INTEGER vs BIGINT drift.

Every Discord ID column (``message_id``, ``guild_id``, ``channel_id``,
``author_id``, ``reply_to_id``, ``thread_id``) is stored as ``BIGINT``.
Postgres ``INTEGER`` is 32-bit and would silently truncate any snowflake
above 2^31 (~2,147,483,647). These tests insert real 19-digit snowflakes
via every write path that touches a Discord ID, read them back, and
assert bitwise equality — if anyone ever downgrades a BIGINT to INTEGER
the assertion fails loudly.

Hand-constructed IDs (not from ``tests/factories.py``) because the
factory defaults are tiny ints for readability in normal tests.
"""

from personabot.db import queries
from personabot.schemas.discord import DiscordMessage

# Real 19-digit Discord snowflakes (above 2^31, below 2^63).
_GUILD_ID = 1234567890123456789
_CHANNEL_ID = 1234567890123456790
_AUTHOR_ID = 1234567890123456791
_MESSAGE_ID = 1234567890123456792
_REPLY_TO_ID = 1234567890123456793
_THREAD_ID = 1234567890123456794


async def test_guild_config_snowflake_round_trip(db_connection):
    """Guild IDs round-trip as full-precision integers."""
    await queries.upsert_guild_config(
        db_connection,
        guild_id=_GUILD_ID,
        guild_name="snowflake-test",
    )

    fetched = await queries.get_guild_config(db_connection, _GUILD_ID)
    assert fetched is not None
    assert fetched.guild_id == _GUILD_ID
    # Type check guards against any future float-coercion regression too.
    assert isinstance(fetched.guild_id, int)


async def test_message_snowflake_round_trip(db_connection):
    """All six BIGINT columns on messages round-trip as full-precision ints."""
    await queries.upsert_guild_config(
        db_connection,
        guild_id=_GUILD_ID,
        guild_name="snowflake-test",
    )

    msg = DiscordMessage(
        message_id=_MESSAGE_ID,
        guild_id=_GUILD_ID,
        channel_id=_CHANNEL_ID,
        channel_name="general",
        author_id=_AUTHOR_ID,
        author_name="snowflake-user",
        content="hello from a 19-digit id",
        timestamp="2026-01-01T12:00:00Z",
        reaction_count=0,
        reply_to_id=_REPLY_TO_ID,
        thread_id=_THREAD_ID,
        word_count=6,
    )
    await queries.upsert_message(db_connection, msg)

    fetched = await queries.get_message_by_id(db_connection, _MESSAGE_ID)
    assert fetched is not None
    assert fetched.message_id == _MESSAGE_ID
    assert fetched.guild_id == _GUILD_ID
    assert fetched.channel_id == _CHANNEL_ID
    assert fetched.author_id == _AUTHOR_ID
    assert fetched.reply_to_id == _REPLY_TO_ID
    assert fetched.thread_id == _THREAD_ID


async def test_scrape_job_snowflake_round_trip(db_connection):
    """scrape_jobs.guild_id and channels list round-trip as full-precision."""
    await queries.upsert_guild_config(
        db_connection,
        guild_id=_GUILD_ID,
        guild_name="snowflake-test",
    )
    await queries.create_scrape_job(
        db_connection,
        job_id="snowflake-job",
        guild_id=_GUILD_ID,
        channels=[_CHANNEL_ID],
    )

    fetched = await queries.get_scrape_job(db_connection, "snowflake-job")
    assert fetched is not None
    assert fetched.guild_id == _GUILD_ID
    assert fetched.channels == [_CHANNEL_ID]


async def test_downloaded_media_snowflake_insert(db_connection):
    """downloaded_media accepts full-precision BIGINT message_id/guild_id."""
    await queries.upsert_guild_config(
        db_connection,
        guild_id=_GUILD_ID,
        guild_name="snowflake-test",
    )
    msg = DiscordMessage(
        message_id=_MESSAGE_ID,
        guild_id=_GUILD_ID,
        channel_id=_CHANNEL_ID,
        channel_name="general",
        author_id=_AUTHOR_ID,
        author_name="snowflake-user",
        content="hello",
        timestamp="2026-01-01T12:00:00Z",
        reaction_count=0,
        word_count=1,
    )
    await queries.upsert_message(db_connection, msg)

    # The BIGINT guard: asserts nothing raises on insert with 19-digit IDs.
    await queries.save_downloaded_media(
        db_connection,
        message_id=_MESSAGE_ID,
        guild_id=_GUILD_ID,
        original_url="https://cdn.discord.com/img.png",
        local_path="data/media/snowflake/img.png",
    )
