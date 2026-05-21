"""
/app/services/checking_service.py
签到计费服务
drawing_costQRcode 根据在线时长生成费用二维码
"""

import tempfile
from pathlib import Path

import qrcode

from app.logger import logger


class CheckingService:
    def drawing_costQRcode(self, name: str = None, online_time: int = 0) -> str:
        """根据在线时长（分钟）生成支付二维码图片，返回图片路径"""
        try:
            # 假设每分钟单价（可后续移到 config）
            rate_per_minute = 0.5
            total_cost = online_time * rate_per_minute

            # 模拟支付链接（替换为真实支付接口 URL）
            pay_url = f"https://pay.example.com/pay?name={name}&amount={total_cost:.2f}"

            # 生成二维码
            qr = qrcode.make(pay_url)
            qr_dir = Path(tempfile.gettempdir()) / "lookuface_qrcodes"
            qr_dir.mkdir(parents=True, exist_ok=True)
            qr_path = qr_dir / f"{name}_{online_time}.png"
            qr.save(str(qr_path))

            logger.info(f"生成二维码: {name} 在线{online_time}分钟 费用{total_cost:.2f}")
            return str(qr_path)
        except Exception as e:
            # [优化] 原实现为 ...（占位符），现添加基本二维码生成逻辑
            logger.error(f"生成二维码失败: {e}", exc_info=True)
            return ""

checking_service=CheckingService()