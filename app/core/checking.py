"""
/app/core/checking.py
签到计费核心模块
check_in 签到
check_out 签退或暂离
calculate_online_time 签退后计算在线时长
"""

import datetime

from app.database import SessionLocal, UserFace


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
        "签到 检测到人脸 设置在线"
        db = self._get_db()
        try:
            face = db.query(UserFace).filter_by(
                user_id=user_id, name=name
            ).first()
            if face and not face.is_online:
                face.is_online = True
                db.commit()
                return True
            else:
                raise AttributeError
        except AttributeError as e:
            print(f"{name}出现异常行为")
        finally:
            db.close()

    def check_out(self, away: bool, away_time: int, name: str, user_id: int = None):
        "签退 检测到人脸 设置离线"
        db = self._get_db()
        try:
            face = db.query(UserFace).filter_by(
                user_id=user_id, name=name
            ).first()
            if face and face.is_online:
                if away: face.checkin_time += away_time
                face.is_online = False
                db.commit()
                return True
            else:
                raise AttributeError
        except AttributeError as e:
            print(f"{name}出现异常行为")
        finally:
            db.close()


    def calculate_online_time(self,name:str,user_id:int=None):
        """通过datetime.now()-当前脸的checkin_time 计算在线时间"""
        db=self._get_db()
        try:
            face = db.query(UserFace).filter_by(
                user_id=user_id, name=name
            ).first()
            online_time=datetime.datetime.now()-face.checkin_time
            face.online_time+=online_time
            face.checkin_time=0
            db.commit()
        finally:
            db.close()
        return online_time




checking=Checking()