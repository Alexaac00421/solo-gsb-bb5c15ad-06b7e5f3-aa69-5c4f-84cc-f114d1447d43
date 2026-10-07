import os
import tempfile

import pytest

# 每个测试使用独立数据库文件：在导入 app 之前设置默认值，
# 之后由 autouse fixture 按测试切换（get_settings 每次调用都重新读取环境变量）。
_TMP = tempfile.mkdtemp(prefix="patrol-test-")
os.environ.setdefault("DB_PATH", os.path.join(_TMP, "default.db"))
os.environ.setdefault("MAX_GAP_SECONDS", "60")

from fastapi.testclient import TestClient  # noqa: E402

from app.api import app  # noqa: E402
from app.database import init_db  # noqa: E402


@pytest.fixture
def client(tmp_path, monkeypatch):
    db_file = tmp_path / "patrol.db"
    monkeypatch.setenv("DB_PATH", str(db_file))
    init_db(str(db_file))
    with TestClient(app) as c:
        yield c
