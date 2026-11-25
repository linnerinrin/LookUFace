"""API 契约：健康检查、人脸库读写、识别结果不携带性别年龄。"""

from __future__ import annotations



from app.db.repository import list_faces


def test_health_reports_only_face_detector(client):
    resp = client.get("/api/v1/health")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] in {"healthy", "degraded"}
    assert set(data["models"]) == {"face_proto", "face_model"}


def test_version_exposes_no_billing_fields(client):
    resp = client.get("/api/v1/version")
    assert resp.status_code == 200
    data = resp.json()
    assert data["version"]
    for banned in ("rate_per_minute", "away_timeout_minutes", "max_session_minutes"):
        assert banned not in data


def test_faces_list_is_empty_initially(client):
    resp = client.get("/api/v1/faces")
    assert resp.status_code == 200
    assert resp.json()["faces"] == []


def test_faces_list_shape_has_no_away_state(client, face):
    resp = client.get("/api/v1/faces")
    assert resp.status_code == 200
    item = resp.json()["faces"][0]
    assert item["name"] == "张三"
    assert item["is_online"] is False
    assert "is_away" not in item


def test_rename_face(client, db_session, face):
    resp = client.put(
        f"/api/v1/faces/{face.name}",
        json={"old_name": "张三", "new_name": "李四"},
    )
    assert resp.status_code == 200
    assert [f.name for f in list_faces(db_session)] == ["李四"]


def test_rename_face_to_existing_name_is_rejected(client, db_session, face):
    from app.db.repository import upsert_face
    from app.vision.face_identity import serialize_features
    import numpy as np

    upsert_face(
        db_session,
        name="王五",
        features=serialize_features(np.eye(4)),
        screenshot=None,
    )
    db_session.commit()

    resp = client.put(
        "/api/v1/faces/王五",
        json={"old_name": "张三", "new_name": "王五"},
    )
    assert resp.status_code == 400


def test_delete_missing_face_returns_404(client):
    resp = client.delete("/api/v1/faces/不存在的人")
    assert resp.status_code == 404


def test_delete_face_removes_record(client, db_session, face):
    resp = client.delete(f"/api/v1/faces/{face.name}")
    assert resp.status_code == 200
    assert list(list_faces(db_session)) == []


def test_checkin_unknown_face_returns_404(client):
    resp = client.post("/api/v1/sessions/checkin", json={"name": "查无此人"})
    assert resp.status_code == 404
    assert resp.json()["code"] == "face_not_found"


def test_face_info_schema_has_no_demographics():
    from app.api.schemas import FaceInfo

    fields = set(FaceInfo.model_fields)
    assert fields == {
        "bbox",
        "identity",
        "identity_confidence",
        "detection_confidence",
    }
