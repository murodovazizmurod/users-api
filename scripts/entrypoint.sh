#!/usr/bin/env sh
# Container entrypoint. The first argument selects the process role, so the
# same image runs the API, the Celery worker and the beat scheduler.
set -eu

wait_for_database() {
    python - <<'PY'
import asyncio
import sys

from sqlalchemy import text

from app.db.session import build_engine


async def main() -> None:
    for attempt in range(1, 31):
        engine = build_engine()
        try:
            async with engine.connect() as connection:
                await connection.execute(text("SELECT 1"))
            print("Database is ready")
            return
        except Exception as exc:  # noqa: BLE001
            print(f"Waiting for database ({attempt}/30): {exc}")
            await asyncio.sleep(2)
        finally:
            await engine.dispose()
    sys.exit("Database did not become ready in time")


asyncio.run(main())
PY
}

case "${1:-api}" in
    api)
        wait_for_database
        # Migrations run here rather than in the application's lifespan so a
        # rolling deployment applies them once instead of once per replica.
        echo "Applying database migrations..."
        alembic upgrade head
        echo "Starting API..."
        exec uvicorn app.main:app --host 0.0.0.0 --port 8000 --proxy-headers
        ;;
    worker)
        wait_for_database
        exec celery -A app.workers.celery_app.celery_app worker --loglevel=INFO --concurrency=2
        ;;
    beat)
        # The schedule file only holds "last run" timestamps for interval
        # schedules, so a container-local path is enough; mount a volume here
        # if a restart must not re-trigger a due job.
        mkdir -p /tmp/celerybeat
        exec celery -A app.workers.celery_app.celery_app beat \
            --loglevel=INFO \
            --schedule=/tmp/celerybeat/schedule \
            --pidfile=
        ;;
    *)
        exec "$@"
        ;;
esac
