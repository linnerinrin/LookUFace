"""
/app/core/checking.py
签到计费核心模块
check_in 签到
check_out 签退或暂离
calculate_online_time 签退后计算在线时长
"""

import datetime

from app.database import SessionLocal, UserFace
from app.logger import logger


class Checking:
    _instance=None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialize()
        return cls._instance

    def _initialize(self):
        ...

    def _get_db(self):
        """获取数据库会话"""
        return SessionLocal()

    def check_in(self, name: str, user_id: int = None):
        db = self._get_db()
        try:
            face = db.query(UserFace).filter_by(
                user_id=user_id, name=name
            ).first()

            if not face:
                return False
            if not face.is_online:
                face.is_online = True
                face.is_away = False
                face.checkin_time = datetime.datetime.now()
                db.commit()
                return True
            elif face.is_away:
                # 暂离归来
                face.is_away = False
                db.commit()
                return True
            else:
                return False
        except Exception as e:
            logger.error(f"签到异常：{e}", exc_info=True)
            return False
        finally:
            db.close()

    def check_out(self, away: bool, away_time: int, name: str, user_id: int = None):
        db = self._get_db()
        try:
            face = db.query(UserFace).filter_by(
                user_id=user_id, name=name
            ).first()
            if not face:
                return False

            if away:
                if face.is_online and not face.is_away:
                    face.is_away = True
                    if away_time:
                        face.checkin_time += datetime.timedelta(minutes=away_time)
                    db.commit()
                    return True
            else:
                if face.is_online and face.checkin_time is not None:
                    now = datetime.datetime.now()
                    delta = now - face.checkin_time
                    minutes = int(delta.total_seconds() / 60)
                    face.online_time = (face.online_time or 0) + minutes

                    face.is_online = False
                    face.is_away = False
                    face.checkin_time = None
                    db.commit()
                    return True
        except Exception as e:
            logger.error(f"签退异常: {e}", exc_info=True)
            return False
        finally:
            db.close()

    def get_status(self, name: str, user_id: int = None):
        """查询当前人脸状态"""
        db = self._get_db()
        try:
            face = db.query(UserFace).filter_by(
                user_id=user_id, name=name
            ).first()
            if not face:
                return {"is_online": False, "is_away": False}
            return {
                "is_online": face.is_online,
                "is_away": face.is_away
            }
        finally:
            db.close()


checking=Checking()