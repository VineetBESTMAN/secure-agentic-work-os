from redis import Redis
from rq import Queue, Worker

from app.core.config import get_settings


def main() -> None:
    settings = get_settings()
    connection = Redis.from_url(settings.redis_url)
    queue = Queue(name=settings.job_queue_name, connection=connection)
    worker = Worker([queue], connection=connection)
    worker.work()


if __name__ == "__main__":
    main()
