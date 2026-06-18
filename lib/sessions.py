"""Group memos into workout sessions by recorded_at time window."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from lib.config import Config, load_config
from lib.manifest import Manifest, MemoRecord


def _parse_recorded_at(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _local_timezone(config: Config):
    if config.sessions.timezone == "local":
        return datetime.now().astimezone().tzinfo
    return ZoneInfo(config.sessions.timezone)


def group_sessions(
    manifest: Manifest,
    *,
    config: Config | None = None,
    window_minutes: int | None = None,
) -> int:
    """
    Assign session_id to memos clustered by recorded_at gaps.

    Returns the number of sessions created or updated.
    """
    config = config or load_config()
    window = timedelta(
        minutes=window_minutes if window_minutes is not None else config.sessions.window_minutes
    )
    tz = _local_timezone(config)

    memos = [
        memo
        for memo in manifest.list_memos()
        if memo.recorded_at and memo.transcribe_status == "done"
    ]
    memos.sort(key=lambda m: m.recorded_at or "")

    if not memos:
        return 0

    sessions_created = 0
    current_cluster: list[MemoRecord] = []
    previous_dt: datetime | None = None
    session_id: str | None = None

    def flush_cluster(cluster: list[MemoRecord], sid: str) -> None:
        if not cluster:
            return
        started = cluster[0].recorded_at
        ended = cluster[-1].recorded_at
        assert started is not None and ended is not None
        manifest.upsert_session(
            session_id=sid,
            started_at=started,
            ended_at=ended,
            memo_count=len(cluster),
        )
        for memo in cluster:
            manifest.update_status(memo.id, session_id=sid)

    for memo in memos:
        recorded_dt = _parse_recorded_at(memo.recorded_at)
        if recorded_dt is None:
            continue
        recorded_local = recorded_dt.astimezone(tz)

        if (
            previous_dt is None
            or recorded_local - previous_dt > window
            or session_id is None
        ):
            if current_cluster and session_id is not None:
                flush_cluster(current_cluster, session_id)
            session_id = str(uuid.uuid4())
            current_cluster = [memo]
            sessions_created += 1
        else:
            current_cluster.append(memo)

        previous_dt = recorded_local

    if current_cluster and session_id is not None:
        flush_cluster(current_cluster, session_id)

    return sessions_created
