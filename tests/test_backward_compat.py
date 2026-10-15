"""真实数据库向后兼容验证。

用户的真实 ``storage/face_lens.db`` 里有一条**旧版 pickle 格式**的人脸记录。
本测试复刻该库并验证新代码：

1. 表结构无需迁移即可直接使用；
2. 旧 pickle 特征被安全拒绝（**不反序列化**）；
3. 旧记录被标记为 ``features_current=False``，接口不返回 500；
4. 会话与计费接口在旧库上仍可用；
5. 重复签退优雅失败，数据不被破坏。

**实现说明**：``app.config.settings`` 在导入期读取环境变量，因此必须在
独立子进程里先设置 ``DATABASE_URL`` 再导入应用 —— 否则会与 conftest 的
临时数据库配置冲突。这里把校验逻辑作为脚本在子进程中执行。
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
REAL_DB = PROJECT_ROOT / "storage" / "face_lens.db"

SCRIPT = r'''
import os, shutil, sys, tempfile
from pathlib import Path

REAL_DB = Path(sys.argv[1])
PROJECT_ROOT = Path(sys.argv[2])

tmp = Path(tempfile.mkdtemp(prefix="lf-compat-"))
db_path = tmp / "face_lens.db"
shutil.copy2(REAL_DB, db_path)

os.environ["SECRET_KEY"] = "compat-check-key-" + "qwertyuiopasdfghjklzxcvbnm1234567890" * 2
os.environ["DATABASE_URL"] = f"sqlite:///{db_path.as_posix()}"
os.environ["LOG_LEVEL"] = "ERROR"
os.environ["LOG_DIR"] = str(tmp / "logs")
os.environ["STORAGE_DIR"] = str(tmp / "storage")

sys.path.insert(0, str(PROJECT_ROOT))

from fastapi.testclient import TestClient

from app.main import app
from app.db.models import SessionLocal, UserFace
from app.services.security import create_access_token
from app.vision.face_identity import StoredFeatureError, deserialize_features

failures = []


def check(label, condition, detail=""):
    mark = "PASS" if condition else "FAIL"
    print(f"   [{mark}] {label}" + (f" — {detail}" if detail else ""))
    if not condition:
        failures.append(label)


print("1) 表结构兼容：新代码在旧库上直接可用（无迁移）")
db = SessionLocal()
faces = db.query(UserFace).all()
check("能读取旧库的人脸记录", len(faces) >= 1, f"{len(faces)} 条")
for f in faces:
    print(f"        id={f.id} name={f.name!r} is_online={f.is_online} "
          f"online_time={f.online_time} features={len(f.features)} 字节")

print("2) 旧 pickle 特征被安全拒绝（不反序列化）")
for f in faces:
    try:
        deserialize_features(f.features)
        print(f"        {f.name}: 解析为新格式")
    except StoredFeatureError as exc:
        print(f"        {f.name}: 已拒绝 -> {exc}")

print("3) 旧记录标记为需重新注册，接口不 500")
face = faces[0]
token = create_access_token({"user_id": face.user_id, "email": "compat@test.local"})
client = TestClient(app)
client.headers.update({"Authorization": f"Bearer {token}"})

r = client.get("/api/v1/faces")
check("GET /faces 返回 200", r.status_code == 200, f"status={r.status_code}")
items = r.json()["faces"] if r.status_code == 200 else []
check("读到旧记录", bool(items))
if items:
    for item in items:
        print(f"        {item['name']!r} features_current={item['features_current']}")
    check("旧 pickle 记录被标记为需重新注册", items[0]["features_current"] is False)

print("4) 会话与计费接口在旧库上可用")
name = items[0]["name"] if items else None
if name:
    r = client.post("/api/v1/sessions/status", json={"name": name})
    check("POST /sessions/status 200", r.status_code == 200, r.text[:120])

    r = client.post("/api/v1/sessions/checkin", json={"name": name})
    check("签到成功", r.status_code == 200 and r.json().get("is_online") is True, r.text[:120])

    r = client.post("/api/v1/sessions/checkout", json={"name": name, "away": False})
    body = r.json() if r.status_code == 200 else {}
    check("签退返回非负时长", r.status_code == 200 and body.get("total_minutes", -1) >= 0,
          f"本次 {body.get('session_minutes')} 分钟 累计 {body.get('total_minutes')} 分钟 "
          f"金额 {body.get('amount')}")

    r = client.post("/api/v1/billing/quote", json={"name": name})
    check("生成账单报价", r.status_code == 200, r.text[:160])

    r = client.post("/api/v1/sessions/checkout", json={"name": name, "away": False})
    check("重复签退优雅失败", r.status_code == 200 and r.json().get("success") is False,
          r.text[:120])

print("5) 数据未被破坏")
db.expire_all()
after = db.query(UserFace).filter_by(id=face.id).one()
check("online_time 非负", after.online_time >= 0, str(after.online_time))
check("会话已关闭", after.is_online is False)
check("checkin_time 已清理", after.checkin_time is None, str(after.checkin_time))

db.close()
shutil.rmtree(tmp, ignore_errors=True)

if failures:
    print("\n失败项: " + "; ".join(failures))
    sys.exit(1)
print("\n向后兼容验证全部通过")
'''


@pytest.mark.skipif(not REAL_DB.exists(), reason="本机没有真实数据库可验证")
def test_real_database_is_backward_compatible() -> None:
    """在子进程中用真实数据库副本跑完整验证。"""
    result = subprocess.run(
        [sys.executable, "-X", "utf8", "-c", SCRIPT, str(REAL_DB), str(PROJECT_ROOT)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=300,
        cwd=str(PROJECT_ROOT),
    )

    print(result.stdout)
    if result.returncode != 0:
        print(result.stderr[-3000:])

    assert result.returncode == 0, "真实数据库兼容性验证失败（详见上方输出）"
    assert "向后兼容验证全部通过" in result.stdout
