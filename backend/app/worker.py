from redis import Redis
from rq import Queue, Worker

from app.core.config import get_settings
from app.services.jobs import job_service


def handle_work_horse_killed(job, _retpid, _ret_val, _rusage) -> None:
    """Persist RQ's otherwise Redis-only worker-loss state for operations visibility."""
    try:
        job_service.handle_worker_loss(job.id, int(job.retries_left or 0))
    except Exception:
        # RQ must still complete its own retry/failure handling if the database is down.
        return


def main() -> None:
    settings = get_settings()
    connection = Redis.from_url(settings.redis_url)
    queue = Queue(name=settings.job_queue_name, connection=connection)
    worker = Worker(
        [queue],
        connection=connection,
        work_horse_killed_handler=handle_work_horse_killed,
    )
    worker.work()


if __name__ == "__main__":
    main()
