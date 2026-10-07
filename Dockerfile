FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    DB_PATH=/data/patrol.db

WORKDIR /srv

# 仅依赖 wheel，无需编译工具链（shapely 有 manylinux 轮子）。
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app

# 数据卷与容器启动时初始化由应用 lifespan 完成（幂等建表）。
RUN mkdir -p /data
VOLUME ["/data"]

EXPOSE 8080

HEALTHCHECK --interval=15s --timeout=3s --start-period=5s --retries=5 \
    CMD python -c "import json,os,urllib.request; \
port=os.environ.get('APP_PORT','8080'); \
urllib.request.urlopen('http://127.0.0.1:%s/health' % port, timeout=2).read()" || exit 1

CMD ["python", "-m", "app"]
