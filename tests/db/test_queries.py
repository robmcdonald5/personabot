"""Tests for database query functions."""

from factories import make_message

from personabot.db import queries
from personabot.schemas.discord import JobStatus

# DB tests use these specific IDs for query assertions
_DB = dict(guild_id=100, channel_id=200, author_id=300, author_name="testuser")


def _msg(**overrides):
    """make_message with DB-specific defaults."""
    return make_message(**{**_DB, **overrides})


# --- Guild config ---


async def test_upsert_and_get_guild_config(db_connection):
    config = await queries.upsert_guild_config(
        db_connection,
        guild_id=100,
        guild_name="Test Server",
        scrape_channels=[1, 2, 3],
        included_users=[99],
    )
    await db_connection.commit()
    assert config.guild_id == 100
    assert config.guild_name == "Test Server"
    assert config.scrape_channels == [1, 2, 3]
    assert config.included_users == [99]

    fetched = await queries.get_guild_config(db_connection, 100)
    assert fetched is not None
    assert fetched.guild_name == "Test Server"


async def test_guild_config_upsert_updates(db_connection):
    await queries.upsert_guild_config(
        db_connection,
        guild_id=100,
        guild_name="Old Name",
    )
    await db_connection.commit()
    updated = await queries.upsert_guild_config(
        db_connection,
        guild_id=100,
        guild_name="New Name",
        scrape_channels=[5, 6],
    )
    await db_connection.commit()
    assert updated.guild_name == "New Name"
    assert updated.scrape_channels == [5, 6]


async def test_guild_config_upsert_preserves_other_fields(db_connection):
    """Setting one field should not wipe other fields."""
    await queries.upsert_guild_config(
        db_connection,
        guild_id=100,
        guild_name="Test Server",
        scrape_channels=[1, 2, 3],
        included_users=[99],
        default_limit=5000,
    )
    await db_connection.commit()
    # Update only included_users — channels and limit should survive
    updated = await queries.upsert_guild_config(
        db_connection,
        guild_id=100,
        guild_name="Test Server",
        included_users=[88, 77],
    )
    await db_connection.commit()
    assert updated.scrape_channels == [1, 2, 3]
    assert updated.included_users == [88, 77]
    assert updated.default_limit == 5000


async def test_get_guild_config_missing(db_connection):
    result = await queries.get_guild_config(db_connection, 999)
    assert result is None


# --- Included users + scrape window ---


async def test_upsert_guild_config_included_users(db_connection):
    """Included users field round-trips via upsert and get."""
    cfg = await queries.upsert_guild_config(
        db_connection,
        guild_id=100,
        guild_name="Test Server",
        included_users=[1, 2, 3],
    )
    await db_connection.commit()
    assert cfg.included_users == [1, 2, 3]

    fetched = await queries.get_guild_config(db_connection, 100)
    assert fetched is not None
    assert fetched.included_users == [1, 2, 3]


async def test_upsert_guild_config_window_dates(db_connection):
    """Scrape start/end dates round-trip as YYYY-MM-DD strings."""
    cfg = await queries.upsert_guild_config(
        db_connection,
        guild_id=100,
        guild_name="Test Server",
        scrape_start_date="2024-01-15",
        scrape_end_date="2024-06-30",
    )
    await db_connection.commit()
    assert cfg.scrape_start_date == "2024-01-15"
    assert cfg.scrape_end_date == "2024-06-30"

    fetched = await queries.get_guild_config(db_connection, 100)
    assert fetched is not None
    assert fetched.scrape_start_date == "2024-01-15"
    assert fetched.scrape_end_date == "2024-06-30"


async def test_upsert_guild_config_preserves_new_fields(db_connection):
    """Partial updates must not wipe other merge-preserved fields."""
    await queries.upsert_guild_config(
        db_connection,
        guild_id=100,
        guild_name="Test Server",
        included_users=[10, 20],
        scrape_start_date="2024-01-15",
        scrape_end_date="2024-06-30",
    )
    await db_connection.commit()
    # Update an unrelated field — v2 fields should survive
    updated = await queries.upsert_guild_config(
        db_connection,
        guild_id=100,
        guild_name="Test Server",
        scrape_channels=[5, 6],
    )
    await db_connection.commit()
    assert updated.included_users == [10, 20]
    assert updated.scrape_start_date == "2024-01-15"
    assert updated.scrape_end_date == "2024-06-30"
    assert updated.scrape_channels == [5, 6]


async def test_reset_guild_config(db_connection):
    """Reset clears all user-configurable fields but keeps the row."""
    await queries.upsert_guild_config(
        db_connection,
        guild_id=100,
        guild_name="Test Server",
        scrape_channels=[1, 2],
        included_users=[42],
        scrape_start_date="2024-01-15",
        scrape_end_date="2024-06-30",
        default_limit=5000,
    )
    await db_connection.commit()
    original = await queries.get_guild_config(db_connection, 100)
    assert original is not None
    original_created = original.created_at

    ok = await queries.reset_guild_config(db_connection, 100)
    await db_connection.commit()
    assert ok is True

    after = await queries.get_guild_config(db_connection, 100)
    assert after is not None
    assert after.guild_id == 100
    assert after.guild_name == "Test Server"
    assert after.scrape_channels == []
    assert after.included_users == []
    assert after.scrape_start_date is None
    assert after.scrape_end_date is None
    assert after.default_limit == 10000
    # Row identity preserved
    assert after.created_at == original_created


async def test_reset_guild_config_missing_row(db_connection):
    """Resetting a guild with no config row returns False."""
    ok = await queries.reset_guild_config(db_connection, 999)
    await db_connection.commit()
    assert ok is False


# --- Scrape jobs ---


async def test_create_and_get_scrape_job(db_connection):
    # Need guild_config first (foreign key)
    await queries.upsert_guild_config(db_connection, 100, "Test Server")
    await db_connection.commit()

    job = await queries.create_scrape_job(
        db_connection,
        job_id="job-1",
        guild_id=100,
        target_user_id=300,
        channels=[1, 2],
    )
    await db_connection.commit()
    assert job.job_id == "job-1"
    assert job.status == JobStatus.RUNNING
    assert job.channels == [1, 2]


async def test_update_scrape_job_status(db_connection):
    await queries.upsert_guild_config(db_connection, 100, "Test Server")
    await queries.create_scrape_job(db_connection, "job-1", 100)
    await db_connection.commit()

    await queries.update_scrape_job_status(
        db_connection,
        "job-1",
        JobStatus.COMPLETED,
        messages_found=500,
        messages_stored=480,
    )
    await db_connection.commit()
    job = await queries.get_scrape_job(db_connection, "job-1")
    assert job is not None
    assert job.status == JobStatus.COMPLETED
    assert job.messages_found == 500
    assert job.completed_at is not None


async def test_get_active_scrape_jobs(db_connection):
    await queries.upsert_guild_config(db_connection, 100, "Test Server")
    await queries.create_scrape_job(db_connection, "job-1", 100)
    await queries.create_scrape_job(db_connection, "job-2", 100)
    await queries.update_scrape_job_status(db_connection, "job-2", JobStatus.COMPLETED)
    await db_connection.commit()

    active = await queries.get_active_scrape_jobs(db_connection, 100)
    assert len(active) == 1
    assert active[0].job_id == "job-1"


async def test_count_scrape_jobs(db_connection):
    """Count reflects all jobs regardless of status."""
    await queries.upsert_guild_config(db_connection, 100, "Test Server")
    assert await queries.count_scrape_jobs(db_connection, 100) == 0

    await queries.create_scrape_job(db_connection, "job-1", 100)
    await queries.create_scrape_job(db_connection, "job-2", 100)
    await queries.update_scrape_job_status(db_connection, "job-2", JobStatus.COMPLETED)
    await db_connection.commit()

    assert await queries.count_scrape_jobs(db_connection, 100) == 2
    # Different guild with no jobs still returns 0
    assert await queries.count_scrape_jobs(db_connection, 999) == 0


# --- Messages ---


async def test_upsert_message(db_connection):
    msg = _msg()
    await queries.upsert_message(db_connection, msg)
    await db_connection.commit()

    fetched = await queries.get_message_by_id(db_connection, 1)
    assert fetched is not None
    assert fetched.content == msg.content
    assert fetched.author_name == "testuser"


async def test_upsert_message_idempotent(db_connection):
    """Re-upserting the same message updates mutable fields."""
    msg = _msg(reaction_count=0)
    await queries.upsert_message(db_connection, msg)
    await db_connection.commit()

    updated = _msg(reaction_count=5)
    await queries.upsert_message(db_connection, updated)
    await db_connection.commit()

    fetched = await queries.get_message_by_id(db_connection, 1)
    assert fetched is not None
    assert fetched.reaction_count == 5


async def test_upsert_messages_batch(db_connection):
    messages = [_msg(message_id=i, content=f"msg {i}") for i in range(10)]
    count = await queries.upsert_messages_batch(db_connection, messages)
    await db_connection.commit()
    assert count == 10

    user_msgs = await queries.get_user_messages(db_connection, 100, 300)
    assert len(user_msgs) == 10


async def test_get_user_messages_with_limit(db_connection):
    messages = [_msg(message_id=i) for i in range(20)]
    await queries.upsert_messages_batch(db_connection, messages)
    await db_connection.commit()

    limited = await queries.get_user_messages(db_connection, 100, 300, limit=5)
    assert len(limited) == 5


async def test_get_messages_by_ids(db_connection):
    """Fetch a subset of messages by ID."""
    messages = [_msg(message_id=i, content=f"msg {i}") for i in range(1, 6)]
    await queries.upsert_messages_batch(db_connection, messages)
    await db_connection.commit()

    result = await queries.get_messages_by_ids(db_connection, {1, 3, 5})
    assert len(result) == 3
    assert set(result.keys()) == {1, 3, 5}
    assert result[3].content == "msg 3"


async def test_get_messages_by_ids_empty(db_connection):
    """Empty set returns empty dict."""
    result = await queries.get_messages_by_ids(db_connection, set())
    assert result == {}


async def test_get_messages_by_ids_missing(db_connection):
    """Nonexistent IDs are silently excluded."""
    msg = _msg(message_id=1)
    await queries.upsert_message(db_connection, msg)
    await db_connection.commit()

    result = await queries.get_messages_by_ids(db_connection, {1, 999})
    assert len(result) == 1
    assert 1 in result
    assert 999 not in result


# --- Media ---


async def test_save_and_get_media(db_connection):
    msg = _msg()
    await queries.upsert_message(db_connection, msg)
    await db_connection.commit()

    media = await queries.save_downloaded_media(
        db_connection,
        message_id=1,
        guild_id=100,
        original_url="https://cdn.discord.com/img.png",
        local_path="data/media/100/1/img.png",
        content_type="image/png",
        file_size=12345,
    )
    await db_connection.commit()
    assert media.media_id is not None
    assert media.content_type == "image/png"

    user_media = await queries.get_media_for_user(db_connection, 100, 300)
    assert len(user_media) == 1
    assert user_media[0].local_path == "data/media/100/1/img.png"


# --- Stats ---


async def test_get_user_stats(db_connection):
    messages = [
        _msg(message_id=i, word_count=10, reaction_count=i) for i in range(1, 6)
    ]
    await queries.upsert_messages_batch(db_connection, messages)
    await db_connection.commit()

    stats = await queries.get_user_stats(db_connection, 100, 300)
    assert stats is not None
    assert stats.message_count == 5
    assert stats.avg_word_count == 10.0
    assert stats.total_reactions == 15  # 1+2+3+4+5


async def test_get_server_stats(db_connection):
    await queries.upsert_guild_config(db_connection, 100, "Test Server")
    await queries.create_scrape_job(db_connection, "job-1", 100)
    await db_connection.commit()

    messages = [
        _msg(message_id=1, author_id=300, channel_id=10),
        _msg(message_id=2, author_id=400, channel_id=20, author_name="other"),
    ]
    await queries.upsert_messages_batch(db_connection, messages)
    await db_connection.commit()

    stats = await queries.get_server_stats(db_connection, 100)
    assert stats.total_messages == 2
    assert stats.unique_users == 2
    assert stats.total_channels == 2
    assert stats.total_scrape_jobs == 1


# --- Retention cleanup ---


async def test_delete_expired_media(db_connection):
    """Expired media records are deleted and paths returned."""
    msg = _msg()
    await queries.upsert_message(db_connection, msg)
    await queries.save_downloaded_media(
        db_connection,
        message_id=1,
        guild_id=100,
        original_url="https://cdn.discord.com/img.png",
        local_path="data/media/100/1/img.png",
    )
    await db_connection.commit()

    # Cutoff in the future — everything is "expired"
    paths = await queries.delete_expired_media(db_connection, "2099-01-01 00:00:00")
    await db_connection.commit()
    assert paths == ["data/media/100/1/img.png"]

    # Verify record is gone
    media = await queries.get_media_for_user(db_connection, 100, 300)
    assert len(media) == 0


async def test_delete_expired_messages(db_connection):
    """Expired messages are deleted and count returned."""
    messages = [_msg(message_id=i) for i in range(5)]
    await queries.upsert_messages_batch(db_connection, messages)
    await db_connection.commit()

    count = await queries.delete_expired_messages(db_connection, "2099-01-01 00:00:00")
    await db_connection.commit()
    assert count == 5

    remaining = await queries.get_user_messages(db_connection, 100, 300)
    assert len(remaining) == 0


async def test_delete_expired_scrape_jobs(db_connection):
    """Completed scrape jobs older than cutoff are deleted."""
    await queries.upsert_guild_config(db_connection, 100, "Test Server")
    await queries.create_scrape_job(db_connection, "job-1", 100)
    await queries.update_scrape_job_status(db_connection, "job-1", JobStatus.COMPLETED)
    await db_connection.commit()

    count = await queries.delete_expired_scrape_jobs(
        db_connection, "2099-01-01 00:00:00"
    )
    await db_connection.commit()
    assert count == 1

    job = await queries.get_scrape_job(db_connection, "job-1")
    assert job is None


async def test_delete_expired_scrape_jobs_never_completed(db_connection):
    """Failed jobs with no completed_at are deleted based on created_at."""
    await queries.upsert_guild_config(db_connection, 100, "Test Server")
    await queries.create_scrape_job(db_connection, "job-stale", 100)
    # Simulate a job that crashed — status changed but no completed_at
    await db_connection.execute(
        "UPDATE scrape_jobs SET status = 'failed' WHERE job_id = 'job-stale'"
    )
    await db_connection.commit()

    count = await queries.delete_expired_scrape_jobs(
        db_connection, "2099-01-01 00:00:00"
    )
    await db_connection.commit()
    assert count == 1


async def test_delete_expired_scrape_jobs_skips_running(db_connection):
    """Running jobs are never deleted by retention cleanup."""
    await queries.upsert_guild_config(db_connection, 100, "Test Server")
    await queries.create_scrape_job(db_connection, "job-active", 100)
    await db_connection.commit()

    count = await queries.delete_expired_scrape_jobs(
        db_connection, "2099-01-01 00:00:00"
    )
    assert count == 0

    job = await queries.get_scrape_job(db_connection, "job-active")
    assert job is not None
    assert job.status == JobStatus.RUNNING
