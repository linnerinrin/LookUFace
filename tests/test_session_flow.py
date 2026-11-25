"""会话流程：签到 / 签退 / 流水落库 / 重复动作不重复记账。"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from app.db.repository import create_session_record, list_session_records, open_session_record
from app.services.session import session_service


def test_check_in_marks_online_and_writes_record(db_session, face):
    result = session_service.check_in(db_session, name=face.name)

    assert result.success is True
    assert result.deduplicated is False
    assert result.state.is_online is True

    records = list_session_records(db_session)
    assert len(records) == 1
    assert records[0].name == face.name
    assert records[0].checkout_at is None
    assert records[0].source == "manual"


def test_duplicate_check_in_does_not_write_second_record(db_session, face):
    session_service.check_in(db_session, name=face.name)
    second = session_service.check_in(db_session, name=face.name)

    assert second.deduplicated is True
    assert len(list_session_records(db_session)) == 1


def test_check_out_closes_record_with_duration(db_session, face):
    session_service.check_in(db_session, name=face.name)
    result = session_service.check_out(db_session, name=face.name, duration_minutes=45)

    assert result.success is True
    assert result.session_minutes == 45
    assert result.total_minutes == 45

    records = list_session_records(db_session)
    assert len(records) == 1
    assert records[0].checkout_at is not None
    assert records[0].duration_minutes == 45
    assert records[0].duration_seconds == 45 * 60


def test_check_out_twice_is_idempotent(db_session, face):
    session_service.check_in(db_session, name=face.name)
    session_service.check_out(db_session, name=face.name, duration_minutes=10)
    again = session_service.check_out(db_session, name=face.name, duration_minutes=10)

    assert again.success is False
    assert "未在场" in again.message
    records = list_session_records(db_session)
    assert len(records) == 1
    assert records[0].duration_minutes == 10


def test_check_out_without_check_in_is_rejected(db_session, face):
    result = session_service.check_out(db_session, name=face.name)

    assert result.success is False
    assert list_session_records(db_session) == []


def test_accumulated_minutes_survive_multiple_sessions(db_session, face):
    session_service.check_in(db_session, name=face.name)
    session_service.check_out(db_session, name=face.name, duration_minutes=20)

    session_service.check_in(db_session, name=face.name)
    result = session_service.check_out(db_session, name=face.name, duration_minutes=15)

    assert result.session_minutes == 15
    assert result.total_minutes == 35
    assert len(list_session_records(db_session)) == 2


def test_check_out_falls_back_when_no_open_record_exists(db_session, face):
    """升级前就在场的会话没有流水，签退时要补写一条完整记录而不是丢账。"""
    from app.db.models import UserFace

    record = db_session.get(UserFace, face.id)
    record.is_online = True
    record.checkin_time = datetime.now() - timedelta(minutes=30)
    db_session.commit()
    assert open_session_record(db_session, name=face.name) is None

    result = session_service.check_out(db_session, name=face.name)

    assert result.success is True
    records = list_session_records(db_session)
    assert len(records) == 1
    assert records[0].checkout_at is not None
    assert records[0].duration_minutes >= 29


def test_records_are_filtered_by_name(db_session, face):
    from app.db.models import UserFace
    from app.vision.face_identity import serialize_features
    import numpy as np

    other = UserFace(name="其他人", features=serialize_features(np.eye(4)))
    db_session.add(other)
    db_session.commit()

    session_service.check_in(db_session, name=face.name)
    session_service.check_in(db_session, name="其他人")

    mine = list_session_records(db_session, name=face.name)
    assert len(mine) == 1
    assert mine[0].name == face.name


def test_api_checkin_then_checkout(client, db_session, face):
    resp = client.post("/api/v1/sessions/checkin", json={"name": face.name})
    assert resp.status_code == 200
    assert resp.json()["is_online"] is True

    resp = client.post(
        "/api/v1/sessions/checkout",
        json={"name": face.name, "duration_minutes": 12},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["session_minutes"] == 12
    assert body["is_online"] is False

    records = list_session_records(db_session)
    assert records[0].duration_minutes == 12


def test_api_status_reports_preview_minutes(client, face):
    client.post("/api/v1/sessions/checkin", json={"name": face.name})
    resp = client.post("/api/v1/sessions/status", json={"name": face.name})

    assert resp.status_code == 200
    body = resp.json()
    assert body["is_online"] is True
    assert body["preview_minutes"] >= 0


def test_api_has_no_settle_expired_endpoint(client):
    resp = client.post("/api/v1/sessions/settle-expired")
    assert resp.status_code == 404


@pytest.mark.parametrize("source", ["entry", "exit", "manual"])
def test_record_keeps_source(db_session, face, source):
    create_session_record(
        db_session,
        name=face.name,
        checkin_at=datetime.now(),
        source=source,
    )
    db_session.commit()

    assert list_session_records(db_session)[0].source == source
