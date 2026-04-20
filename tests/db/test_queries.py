"""Tests for database query functions."""

from datetime import datetime, timezone

from factories import make_message

from personabot.db import queries
from personabot.schemas.discord import JobStatus

# Retention cutoffs use a far-future datetime so every existing row
# matches the "older than cutoff" predicate in delete_expired_*.
_FUTURE_CUTOFF = datetime(2099, 1, 1, tzinfo=timezone.utc)

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
    updated = await queries.upsert_guild_config(
        db_connection,
        guild_id=100,
        guild_name="New Name",
        scrape_channels=[5, 6],
    )
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
    # Update only included_users — channels and limit should survive
    updated = await queries.upsert_guild_config(
        db_connection,
        guild_id=100,
        guild_name="Test Server",
        included_users=[88, 77],
    )
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
    # Update only scrape_channels — other fields must be preserved on upsert.
    updated = await queries.upsert_guild_config(
        db_connection,
        guild_id=100,
        guild_name="Test Server",
        scrape_channels=[5, 6],
    )
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
    original = await queries.get_guild_config(db_connection, 100)
    assert original is not None
    original_created = original.created_at

    ok = await queries.reset_guild_config(db_connection, 100)
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
    assert ok is False


# --- Scrape jobs ---


async def test_create_and_get_scrape_job(db_connection):
    # Need guild_config first (foreign key)
    await queries.upsert_guild_config(db_connection, 100, "Test Server")

    job = await queries.create_scrape_job(
        db_connection,
        job_id="job-1",
        guild_id=100,
        channels=[1, 2],
    )
    assert job.job_id == "job-1"
    assert job.status == JobStatus.RUNNING
    assert job.channels == [1, 2]


async def test_update_scrape_job_status(db_connection):
    await queries.upsert_guild_config(db_connection, 100, "Test Server")
    await queries.create_scrape_job(db_connection, "job-1", 100)

    await queries.update_scrape_job_status(
        db_connection,
        "job-1",
        JobStatus.COMPLETED,
        messages_found=500,
        messages_stored=480,
    )
    job = await queries.get_scrape_job(db_connection, "job-1")
    assert job is not None
    assert job.status == JobStatus.COMPLETED
    assert job.messages_found == 500
    assert job.completed_at is not None


async def test_count_scrape_jobs(db_connection):
    """Count reflects all jobs regardless of status."""
    await queries.upsert_guild_config(db_connection, 100, "Test Server")
    assert await queries.count_scrape_jobs(db_connection, 100) == 0

    await queries.create_scrape_job(db_connection, "job-1", 100)
    await queries.create_scrape_job(db_connection, "job-2", 100)
    await queries.update_scrape_job_status(db_connection, "job-2", JobStatus.COMPLETED)

    assert await queries.count_scrape_jobs(db_connection, 100) == 2
    # Different guild with no jobs still returns 0
    assert await queries.count_scrape_jobs(db_connection, 999) == 0


# --- Messages ---


async def test_upsert_message(db_connection):
    msg = _msg()
    await queries.upsert_message(db_connection, msg)

    fetched = await queries.get_message_by_id(db_connection, 1)
    assert fetched is not None
    assert fetched.content == msg.content
    assert fetched.author_name == "testuser"


async def test_upsert_message_idempotent(db_connection):
    """Re-upserting the same message updates mutable fields."""
    msg = _msg(reaction_count=0)
    await queries.upsert_message(db_connection, msg)

    updated = _msg(reaction_count=5)
    await queries.upsert_message(db_connection, updated)

    fetched = await queries.get_message_by_id(db_connection, 1)
    assert fetched is not None
    assert fetched.reaction_count == 5


async def test_upsert_messages_batch(db_connection):
    messages = [_msg(message_id=i, content=f"msg {i}") for i in range(10)]
    count = await queries.upsert_messages_batch(db_connection, messages)
    assert count == 10

    user_msgs = await queries.get_user_messages(db_connection, 100, 300)
    assert len(user_msgs) == 10


async def test_get_user_messages_with_limit(db_connection):
    messages = [_msg(message_id=i) for i in range(20)]
    await queries.upsert_messages_batch(db_connection, messages)

    limited = await queries.get_user_messages(db_connection, 100, 300, limit=5)
    assert len(limited) == 5


async def test_get_messages_by_ids(db_connection):
    """Fetch a subset of messages by ID."""
    messages = [_msg(message_id=i, content=f"msg {i}") for i in range(1, 6)]
    await queries.upsert_messages_batch(db_connection, messages)

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

    result = await queries.get_messages_by_ids(db_connection, {1, 999})
    assert len(result) == 1
    assert 1 in result
    assert 999 not in result


# --- job_id provenance (first-wins on conflict + filter) ---


async def test_upsert_first_wins_on_job_id(db_connection):
    """Re-upserting a message with a different job_id must NOT change it.

    First-wins semantics: the scrape that originally discovered a message
    owns it forever, even if a later scrape re-captures the same row.
    This makes /pb export generate job_id:X return a stable corpus.
    """
    await queries.upsert_guild_config(db_connection, 100, "Test Server")
    await queries.create_scrape_job(db_connection, "job-first", 100)
    await queries.create_scrape_job(db_connection, "job-second", 100)

    msg = _msg(message_id=42, content="hello")
    await queries.upsert_message(db_connection, msg, job_id="job-first")

    # Re-upsert with a different job_id AND changed content. job_id must
    # survive at job-first; content must take the new value (content IS
    # in the ON CONFLICT SET clause).
    updated = _msg(message_id=42, content="hello v2")
    await queries.upsert_message(db_connection, updated, job_id="job-second")

    first = await queries.get_user_messages(db_connection, 100, 300, job_id="job-first")
    second = await queries.get_user_messages(
        db_connection, 100, 300, job_id="job-second"
    )
    assert len(first) == 1
    assert first[0].content == "hello v2"  # mutable field updated
    assert second == []  # job_id stayed at job-first


async def test_get_user_messages_filtered_by_job_id(db_connection):
    """Passing job_id to get_user_messages filters to that scrape's rows."""
    await queries.upsert_guild_config(db_connection, 100, "Test Server")
    await queries.create_scrape_job(db_connection, "job-A", 100)
    await queries.create_scrape_job(db_connection, "job-B", 100)

    batch_a = [_msg(message_id=i) for i in range(1, 4)]
    batch_b = [_msg(message_id=i) for i in range(4, 7)]
    await queries.upsert_messages_batch(db_connection, batch_a, job_id="job-A")
    await queries.upsert_messages_batch(db_connection, batch_b, job_id="job-B")

    all_msgs = await queries.get_user_messages(db_connection, 100, 300)
    only_a = await queries.get_user_messages(db_connection, 100, 300, job_id="job-A")
    only_b = await queries.get_user_messages(db_connection, 100, 300, job_id="job-B")
    unknown = await queries.get_user_messages(
        db_connection, 100, 300, job_id="does-not-exist"
    )

    assert len(all_msgs) == 6
    assert {m.message_id for m in only_a} == {1, 2, 3}
    assert {m.message_id for m in only_b} == {4, 5, 6}
    assert unknown == []


async def test_get_top_users_filtered_by_job_id(db_connection):
    """Passing job_id to get_top_users scopes counts to that scrape."""
    await queries.upsert_guild_config(db_connection, 100, "Test Server")
    await queries.create_scrape_job(db_connection, "job-A", 100)
    await queries.create_scrape_job(db_connection, "job-B", 100)

    # job-A captures 3 messages for author 300
    a_msgs = [_msg(message_id=i, author_id=300) for i in range(1, 4)]
    # job-B captures 2 messages for author 400 (different user)
    b_msgs = [_msg(message_id=i, author_id=400, author_name="bob") for i in range(4, 6)]
    await queries.upsert_messages_batch(db_connection, a_msgs, job_id="job-A")
    await queries.upsert_messages_batch(db_connection, b_msgs, job_id="job-B")

    all_users = await queries.get_top_users(db_connection, 100, n=25)
    only_a = await queries.get_top_users(db_connection, 100, n=25, job_id="job-A")
    only_b = await queries.get_top_users(db_connection, 100, n=25, job_id="job-B")

    assert {u.author_id for u in all_users} == {300, 400}
    assert len(only_a) == 1 and only_a[0].author_id == 300
    assert only_a[0].message_count == 3
    assert len(only_b) == 1 and only_b[0].author_id == 400
    assert only_b[0].message_count == 2


# --- Media ---


async def test_save_downloaded_media(db_connection):
    """save_downloaded_media inserts a row without raising."""
    msg = _msg()
    await queries.upsert_message(db_connection, msg)
    await queries.save_downloaded_media(
        db_connection,
        message_id=1,
        guild_id=100,
        original_url="https://cdn.discord.com/img.png",
        local_path="data/media/100/1/img.png",
        content_type="image/png",
        file_size=12345,
    )


# --- Stats ---


async def test_get_user_stats(db_connection):
    messages = [
        _msg(message_id=i, word_count=10, reaction_count=i) for i in range(1, 6)
    ]
    await queries.upsert_messages_batch(db_connection, messages)

    stats = await queries.get_user_stats(db_connection, 100, 300)
    assert stats is not None
    assert stats.message_count == 5
    assert stats.avg_word_count == 10.0
    assert stats.total_reactions == 15  # 1+2+3+4+5


async def test_get_server_stats(db_connection):
    await queries.upsert_guild_config(db_connection, 100, "Test Server")
    await queries.create_scrape_job(db_connection, "job-1", 100)

    messages = [
        _msg(message_id=1, author_id=300, channel_id=10),
        _msg(message_id=2, author_id=400, channel_id=20, author_name="other"),
    ]
    await queries.upsert_messages_batch(db_connection, messages)

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

    # Cutoff in the future — everything is "expired"
    paths = await queries.delete_expired_media(db_connection, _FUTURE_CUTOFF)
    assert paths == ["data/media/100/1/img.png"]

    # Re-running returns an empty list (row is gone)
    paths2 = await queries.delete_expired_media(db_connection, _FUTURE_CUTOFF)
    assert paths2 == []


async def test_delete_expired_messages(db_connection):
    """Expired messages are deleted and count returned."""
    messages = [_msg(message_id=i) for i in range(5)]
    await queries.upsert_messages_batch(db_connection, messages)

    count = await queries.delete_expired_messages(db_connection, _FUTURE_CUTOFF)
    assert count == 5

    remaining = await queries.get_user_messages(db_connection, 100, 300)
    assert len(remaining) == 0


async def test_delete_expired_scrape_jobs(db_connection):
    """Completed scrape jobs older than cutoff are deleted."""
    await queries.upsert_guild_config(db_connection, 100, "Test Server")
    await queries.create_scrape_job(db_connection, "job-1", 100)
    await queries.update_scrape_job_status(db_connection, "job-1", JobStatus.COMPLETED)

    count = await queries.delete_expired_scrape_jobs(db_connection, _FUTURE_CUTOFF)
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

    count = await queries.delete_expired_scrape_jobs(db_connection, _FUTURE_CUTOFF)
    assert count == 1


async def test_delete_expired_scrape_jobs_skips_running(db_connection):
    """Running jobs are never deleted by retention cleanup."""
    await queries.upsert_guild_config(db_connection, 100, "Test Server")
    await queries.create_scrape_job(db_connection, "job-active", 100)

    count = await queries.delete_expired_scrape_jobs(db_connection, _FUTURE_CUTOFF)
    assert count == 0

    job = await queries.get_scrape_job(db_connection, "job-active")
    assert job is not None
    assert job.status == JobStatus.RUNNING


# --- Manual guild data reset (/pb db reset) ---


async def test_reset_guild_data_wipes_all_scoped_data(db_connection):
    """reset_guild_data deletes messages, scrape_jobs, and downloaded_media."""
    await queries.upsert_guild_config(db_connection, 100, "Test Server")
    await queries.create_scrape_job(db_connection, "job-1", 100)

    messages = [_msg(message_id=i) for i in range(1, 4)]
    await queries.upsert_messages_batch(db_connection, messages, job_id="job-1")
    await queries.save_downloaded_media(
        db_connection,
        message_id=1,
        guild_id=100,
        original_url="https://cdn.discord.com/a.png",
        local_path="data/media/100/1/a.png",
    )

    media_paths, msg_count, job_count = await queries.reset_guild_data(
        db_connection, 100
    )
    assert media_paths == ["data/media/100/1/a.png"]
    assert msg_count == 3
    assert job_count == 1

    # Everything scoped to guild 100 is gone.
    assert await queries.get_user_messages(db_connection, 100, 300) == []
    assert await queries.get_scrape_job(db_connection, "job-1") is None
    assert await queries.count_scrape_jobs(db_connection, 100) == 0


async def test_reset_guild_data_preserves_guild_config(db_connection):
    """reset_guild_data does NOT touch the guild_config row."""
    await queries.upsert_guild_config(
        db_connection,
        guild_id=100,
        guild_name="Test Server",
        scrape_channels=[1, 2, 3],
        included_users=[42, 43],
        scrape_start_date="2024-01-15",
        scrape_end_date="2024-06-30",
        default_limit=5000,
    )
    messages = [_msg(message_id=i) for i in range(1, 4)]
    await queries.upsert_messages_batch(db_connection, messages)

    await queries.reset_guild_data(db_connection, 100)

    cfg = await queries.get_guild_config(db_connection, 100)
    assert cfg is not None
    assert cfg.guild_name == "Test Server"
    assert cfg.scrape_channels == [1, 2, 3]
    assert cfg.included_users == [42, 43]
    assert cfg.scrape_start_date == "2024-01-15"
    assert cfg.scrape_end_date == "2024-06-30"
    assert cfg.default_limit == 5000


async def test_reset_guild_data_preserves_other_guilds(db_connection):
    """Data isolation: resetting guild A must not touch guild B."""
    await queries.upsert_guild_config(db_connection, 100, "Guild A")
    await queries.upsert_guild_config(db_connection, 200, "Guild B")
    await queries.create_scrape_job(db_connection, "job-a", 100)
    await queries.create_scrape_job(db_connection, "job-b", 200)

    msgs_a = [_msg(message_id=i, guild_id=100) for i in range(1, 4)]
    msgs_b = [_msg(message_id=i, guild_id=200) for i in range(10, 13)]
    await queries.upsert_messages_batch(db_connection, msgs_a, job_id="job-a")
    await queries.upsert_messages_batch(db_connection, msgs_b, job_id="job-b")

    _, msg_count, job_count = await queries.reset_guild_data(db_connection, 100)
    assert msg_count == 3
    assert job_count == 1

    # Guild B untouched.
    b_msgs = await queries.get_user_messages(db_connection, 200, 300)
    assert len(b_msgs) == 3
    assert await queries.get_scrape_job(db_connection, "job-b") is not None


async def test_reset_guild_data_returns_media_paths_for_sweep(db_connection):
    """Media paths come back so the caller can clean up the filesystem."""
    await queries.upsert_guild_config(db_connection, 100, "Test Server")
    msg = _msg()
    await queries.upsert_message(db_connection, msg)
    for name in ("a.png", "b.png", "c.jpg"):
        await queries.save_downloaded_media(
            db_connection,
            message_id=1,
            guild_id=100,
            original_url=f"https://cdn.discord.com/{name}",
            local_path=f"data/media/100/1/{name}",
        )

    media_paths, _, _ = await queries.reset_guild_data(db_connection, 100)
    assert sorted(media_paths) == [
        "data/media/100/1/a.png",
        "data/media/100/1/b.png",
        "data/media/100/1/c.jpg",
    ]


async def test_reset_guild_data_empty_guild_is_noop(db_connection):
    """Calling reset on a guild with no data returns empty results, not error."""
    await queries.upsert_guild_config(db_connection, 100, "Test Server")

    media_paths, msg_count, job_count = await queries.reset_guild_data(
        db_connection, 100
    )
    assert media_paths == []
    assert msg_count == 0
    assert job_count == 0
    # The guild_config row is still there.
    assert await queries.get_guild_config(db_connection, 100) is not None
