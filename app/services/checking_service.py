"""
/app/service/checking_service.py
签到计费服务
drawing_costQRcode 计算价格
"""



class CheckingService:
    def drawing_costQRcode(self, name: str = None, online_time: int = 0) -> str:
        """通过计算得到的online_time计算价格 返回二维码path"""
        ...

checking_service=CheckingService()