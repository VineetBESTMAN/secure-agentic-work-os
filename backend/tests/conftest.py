import os
import tempfile
from pathlib import Path

test_data_dir = Path(tempfile.mkdtemp(prefix="workos-tests-"))

os.environ.pop("DATABASE_URL", None)
os.environ["APP_DATABASE_PATH"] = str(test_data_dir / "test-workos.db")
os.environ["APP_UPLOAD_DIR"] = str(test_data_dir / "uploads")
os.environ["APP_OBJECT_STORAGE_BACKEND"] = "local"
os.environ["APP_SECRET_KEY"] = "test-only-jwt-secret-with-at-least-32-characters"
os.environ["APP_ASYNC_JOBS_ENABLED"] = "false"
os.environ["APP_RATE_LIMIT_ENABLED"] = "false"
os.environ["APP_METRICS_ENABLED"] = "true"
os.environ.pop("APP_METRICS_TOKEN", None)
