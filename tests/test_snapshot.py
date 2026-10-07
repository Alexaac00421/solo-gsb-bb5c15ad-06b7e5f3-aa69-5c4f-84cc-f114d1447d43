"""查询快照一致性：读事务进行中，其他连接的写入不可见。"""
import threading

from app.database import connect, init_db, snapshot
from app import repository


def test_snapshot_isolation(tmp_path, monkeypatch):
    db_file = str(tmp_path / "iso.db")
    monkeypatch.setenv("DB_PATH", db_file)
    init_db(db_file)

    # 先写入一个点
    from app.models import PointIn
    from datetime import datetime, timezone, timedelta

    tz = timezone(timedelta(hours=8))
    p = PointIn(event_id="p1", track_no="T1", x=0.0, y=0.0,
                sampled_at=datetime(2026, 1, 1, tzinfo=tz))
    writer = connect(db_file)
    with writer:  # autocommit 模式下 with 直接提交
        repository.insert_points(writer, [p])
    writer.close()

    reader = connect(db_file)
    barrier = threading.Event()

    def concurrent_writer():
        # 等读事务开始并完成第一次读取后再写入
        barrier.wait(timeout=5)
        w = connect(db_file)
        from app.models import PointIn as PI
        p2 = PI(event_id="p2", track_no="T1", x=1.0, y=1.0,
                sampled_at=datetime(2026, 1, 1, 0, 0, 10, tzinfo=tz))
        with w:
            repository.insert_points(w, [p2])
        w.close()

    th = threading.Thread(target=concurrent_writer)
    th.start()
    with snapshot(reader):
        first = repository.get_track_points(reader, "T1")
        assert len(first) == 1
        barrier.set()
        th.join(timeout=5)
        # 同一读事务内再次读取，看不到并发写入
        second = repository.get_track_points(reader, "T1")
        assert len(second) == 1
    reader.close()

    # 新事务能看到写入
    after = connect(db_file)
    with snapshot(after):
        assert len(repository.get_track_points(after, "T1")) == 2
    after.close()
