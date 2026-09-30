"""Retryable garbage collection for abandoned uploads and deleted messages."""

import asyncio
import sys
from datetime import timedelta

import structlog
from sqlalchemy import and_, delete, or_, select
from starlette.concurrency import run_in_threadpool

from klack.core.config import Settings
from klack.core.db.session import SessionFactory, create_engine, create_session_factory
from klack.modules.files.models import FileRecord
from klack.modules.files.service import now
from klack.modules.files.storage import FileStorage


async def cleanup(factory: SessionFactory, settings: Settings) -> int:
    count = 0
    async with factory() as session:
        rows = (
            await session.scalars(
                select(FileRecord)
                .where(
                    or_(
                        and_(FileRecord.status == "deleting", FileRecord.expires_at < now()),
                        and_(
                            FileRecord.status.in_(["pending", "uploading", "ready"]),
                            FileRecord.expires_at < now(),
                        ),
                    )
                )
                .order_by(FileRecord.expires_at)
                .limit(100)
                .with_for_update(skip_locked=True)
            )
        ).all()
        keys = [(row.id, row.storage_key) for row in rows]
        for row in rows:
            row.status = "deleting"
            row.expires_at = now() + timedelta(minutes=5)
        await session.commit()
        for file_id, key in keys:
            try:
                await run_in_threadpool(FileStorage(settings).delete, key)
                removed = await session.get(FileRecord, file_id)
                if removed:
                    removed.status = "removed"
                    removed.filename = ""
                await session.commit()
                count += 1
            except Exception:
                await session.rollback()
                structlog.get_logger(__name__).warning("file_cleanup_retry", file_id=str(file_id))
        # Retain reservations for at least an hour so cancellation cannot evade rate limits.
        await session.execute(
            delete(FileRecord)
            .execution_options(synchronize_session=False)
            .where(
                FileRecord.status == "removed", FileRecord.created_at < now() - timedelta(days=1)
            )
        )
        await session.commit()
    return count


async def run(once: bool) -> None:
    settings = Settings()  # type: ignore[call-arg]
    engine = create_engine(settings)
    try:
        while True:
            try:
                await cleanup(create_session_factory(engine), settings)
            except Exception:
                structlog.get_logger(__name__).warning("file_cleanup_unavailable")
                if once:
                    raise
            if once:
                return
            await asyncio.sleep(60)
    finally:
        await engine.dispose()


def main() -> None:
    asyncio.run(run("once" in sys.argv))
