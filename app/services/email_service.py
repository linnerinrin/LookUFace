"""
app/services/email_service.py
邮箱验证码服务
send_email_sync 发送邮件
generate_code 生成验证码
send_verification_code 异步发送验证码
verify_code 与数据库验证码对比
cleanup_expired_codes 删除未使用的验证码
"""

import random
import asyncio
import smtplib
from datetime import datetime, timedelta
from email.header import Header
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from sqlalchemy.orm import Session
from app.database import VerificationCode, SessionLocal
from app.config import settings


SMTP_HOST = settings.SMTP_HOST
SMTP_PORT = settings.SMTP_PORT
SMTP_USER = settings.SMTP_USER
SMTP_PASSWORD = settings.SMTP_PASSWORD

class EmailService:
    def send_email_sync(self,to_email: str, subject: str, body: str) -> bool:
        """同步发送邮件"""
        try:
            msg = MIMEMultipart()
            msg['From'] = SMTP_USER
            msg['To'] = to_email
            msg['Subject'] = Header(subject, 'utf-8')
            msg.attach(MIMEText(body, 'html', 'utf-8'))

            #连接邮件服务器
            server = smtplib.SMTP(SMTP_HOST, SMTP_PORT)
            server.starttls() #加密传输
            server.login(SMTP_USER, SMTP_PASSWORD)
            server.send_message(msg)#发送邮件
            server.quit()

            print(f"邮件已发送到 {to_email}")
            return True
        except Exception as e:
            print(f"发送邮件失败: {e}")
            return False


    def generate_code(self) -> str:
        """生成6位数字验证码"""
        return f"{random.randint(100000, 999999)}"


    async def send_verification_code(self,db: Session, email: str, code_type: str) -> bool:
        """发送验证码并保存到数据库"""
        code = self.generate_code()

        # 删除旧验证码
        db.query(VerificationCode).filter(
            VerificationCode.email == email,
            VerificationCode.type == code_type,
            VerificationCode.used == False
        ).delete()

        # 保存新验证码
        verification = VerificationCode(
            email=email,
            code=code,
            type=code_type,
            expires_at=datetime.now() + timedelta(minutes=10)
        )
        db.add(verification)
        db.commit()

        # 发送邮件
        subject = "LookUFace Verification Code"
        body = f"""
        <html>
        <body style="font-family: Arial, sans-serif; padding: 20px;">
            <h2 style="color: #4a90e2;">LookUFace Verification Code</h2>
            <p>Your verification code is:</p>
            <div style="background: #f0f0f5; padding: 15px; border-radius: 8px; margin: 20px 0;">
                <span style="font-size: 32px; font-weight: bold; letter-spacing: 4px;">{code}</span>
            </div>
            <p>This code will expire in <strong>10 minutes</strong>.</p>
        </body>
        </html>
        """

        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, self.send_email_sync, email, subject, body)


    def verify_code(self,db: Session, email: str, code: str, code_type: str) -> bool:
        """验证验证码"""
        verification = db.query(VerificationCode).filter(
            VerificationCode.email == email,
            VerificationCode.code == code,
            VerificationCode.type == code_type,
            VerificationCode.used == False,
            VerificationCode.expires_at > datetime.now()
        ).first()

        if verification:
            verification.used = True
            db.delete(verification)
            db.commit()
            print(f"验证码验证成功: {email}")
            return True

        print(f"验证码验证失败: {email}, code={code}")
        return False

async def cleanup_expired_codes():
    while True:
        await asyncio.sleep(3600)
        db = SessionLocal()
        try:
            db.query(VerificationCode).filter(
                VerificationCode.expires_at < datetime.now()
            ).delete()
            db.commit()
        finally:
            db.close()

email_service=EmailService()