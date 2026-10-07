"""`python -m app` entry point: initialise the DB and serve the API."""
from __future__ import annotations

import uvicorn

from .config import get_settings
from .database import init_db


def main() -> None:
    settings = get_settings()
    # 显式初始化一次，便于在启动日志/失败时立即暴露数据库问题。
    init_db(settings.db_path)
    uvicorn.run(
        "app.api:app",
        host=settings.host,
        port=settings.port,
        proxy_headers=True,
    )


if __name__ == "__main__":
    main()
