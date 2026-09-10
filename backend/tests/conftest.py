import os
import tempfile
from pathlib import Path

test_data_dir = Path(tempfile.mkdtemp(prefix="workos-tests-"))

os.environ.pop("DATABASE_URL", None)
os.environ.pop("REDIS_URL", None)
os.environ.pop("DATABASE_URL_FILE", None)
os.environ.pop("REDIS_URL_FILE", None)
os.environ["APP_MODEL_PROVIDER"] = "deterministic"
os.environ["APP_EMBEDDING_PROVIDER"] = "local"
os.environ["APP_DATABASE_PATH"] = str(test_data_dir / "test-workos.db")
os.environ["APP_LANGGRAPH_SQLITE_PATH"] = str(
    test_data_dir / "test-langgraph-checkpoints.db"
)
os.environ["APP_UPLOAD_DIR"] = str(test_data_dir / "uploads")
os.environ["APP_OBJECT_STORAGE_BACKEND"] = "local"
os.environ["APP_LOCAL_EMBEDDING_BACKEND"] = "lexical"
os.environ["APP_SECRET_KEY"] = "test-only-jwt-secret-with-at-least-32-characters"
os.environ["APP_ASYNC_JOBS_ENABLED"] = "false"
os.environ["APP_RATE_LIMIT_ENABLED"] = "false"
os.environ["APP_METRICS_ENABLED"] = "true"
os.environ.pop("APP_METRICS_TOKEN", None)

# Tests must not load developer credentials, production flags, or storage paths.
from app.core.config import Settings

Settings.model_config["env_file"] = None
