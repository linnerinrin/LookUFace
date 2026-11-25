"""数据看板接口：流水补录/列表与统计总览。"""

from __future__ import annotations

from app.db.models import SessionRecord


def test_create_record(client, face):
    resp = client.post("/api/v1/records", json={
        "name": "张三",
        "checkin_at": "2026-11-20T09:00:00",
        "checkout_at": "2026-11-20T11:30:00",
    })
    assert resp.status_code == 200
    data = resp.json()
    assert data["name"] == "张三"
    assert data["duration_minutes"] == 150
    assert data["duration_seconds"] == 9000
    assert data["checkout_at"] is not None
    assert data["source"] == "manual"


def test_create_record_unknown_face_404(client):
    resp = client.post("/api/v1/records", json={
        "name": "不存在的人",
        "checkin_at": "2026-11-20T09:00:00",
        "checkout_at": "2026-11-20T10:00:00",
    })
    assert resp.status_code == 404
    assert resp.json()["code"] == "face_not_found"


def test_create_record_rejects_reversed_times(client, face):
    resp = client.post("/api/v1/records", json={
        "name": "张三",
        "checkin_at": "2026-11-20T11:00:00",
        "checkout_at": "2026-11-20T09:00:00",
    })
    assert resp.status_code == 400


def test_create_record_does_not_touch_online_state(client, face, db_session):
    """补录只写流水，不改 user_faces 的当前在场状态"""
    client.post("/api/v1/records", json={
        "name": "张三",
        "checkin_at": "2026-11-20T09:00:00",
        "checkout_at": "2026-11-20T10:00:00",
    })
    db_session.refresh(face)
    assert face.is_online is False
    assert face.checkin_time is None


def test_list_records(client, face):
    client.post("/api/v1/records", json={
        "name": "张三",
        "checkin_at": "2026-11-20T09:00:00",
        "checkout_at": "2026-11-20T10:00:00",
    })
    resp = client.get("/api/v1/records")
    assert resp.status_code == 200
    records = resp.json()["records"]
    assert len(records) == 1
    assert records[0]["name"] == "张三"


def test_list_records_filter_by_name(client, face, db_session):
    from app.db.models import UserFace

    other = UserFace(name="李四", features=b"")
    db_session.add(other)
    db_session.commit()
    for name in ("张三", "李四"):
        client.post("/api/v1/records", json={
            "name": name,
            "checkin_at": "2026-11-20T09:00:00",
            "checkout_at": "2026-11-20T10:00:00",
        })
    resp = client.get("/api/v1/records", params={"name": "李四"})
    assert resp.status_code == 200
    assert all(r["name"] == "李四" for r in resp.json()["records"])
    assert len(resp.json()["records"]) == 1


def test_stats_overview_shape(client):
    resp = client.get("/api/v1/stats/overview")
    assert resp.status_code == 200
    data = resp.json()
    assert set(data["kpis"]) == {
        "today_visits", "online_now", "today_minutes", "avg_minutes"
    }
    assert data["days"] == 7
    assert set(data["charts"]) == {
        "user_ranking", "daily_trend", "hour_distribution",
        "duration_buckets", "heatmap",
    }
    for uri in data["charts"].values():
        assert uri.startswith("data:image/png;base64,")
        assert len(uri) > 500


def test_stats_overview_reflects_records(client, face):
    """补录后 KPI 与图表总条数应随之增加"""
    before = client.get("/api/v1/stats/overview").json()
    assert before["total_records"] == 0

    client.post("/api/v1/records", json={
        "name": "张三",
        "checkin_at": "2026-11-25T09:00:00",
        "checkout_at": "2026-11-25T10:00:00",
    })
    after = client.get("/api/v1/stats/overview").json()
    assert after["total_records"] == 1
    assert after["kpis"]["avg_minutes"] == 60
