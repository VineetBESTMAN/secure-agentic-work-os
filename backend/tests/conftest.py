import os
import tempfile
from pathlib import Path

test_data_dir = Path(tempfile.mkdtemp(prefix="workos-tests-"))

os.environ.pop("DATABASE_URL", None)
os.environ["APP_DATABASE_PATH"] = str(test_data_dir / "test-workos.db")
os.environ["APP_UPLOAD_DIR"] = str(test_data_dir / "uploads")
os.environ["APP_ASYNC_JOBS_ENABLED"] = "false"
