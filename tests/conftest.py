import os
import tempfile

_tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
_tmp.close()
os.environ["PATROL_DB_PATH"] = _tmp.name
os.environ["PATROL_GAP_THRESHOLD"] = "300"

import pytest
from fastapi.testclient import TestClient

from app import db
from app.main import DB_PATH, app


@pytest.fixture(scope="session")
def client():
    # 与服务启动相同的初始化路径；每个 API 请求自行开连接
    conn = db.connect(DB_PATH)
    db.init_db(conn)
    conn.close()
    with TestClient(app) as c:
        yield c
