"""
邮箱验证码服务
- 生成6位验证码
- 发送邮件（SMTP）
- 验证码存储与验证
"""

import random
import smtplib
from datetime import datetime, timedelta
from email.header import Header
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from sqlalchemy.orm import Session
from app.database import VerificationCode

# SMTP 配置
SMTP_HOST = "smtp.qq.com"
SMTP_PORT = 587
SMTP_USER = "1721884605@qq.com"      # 替换为你的邮箱
SMTP_PASSWORD = "fociqkrkufiajibf"  # 替换为你的授权码


def send_email_sync(to_email: str, subject: str, body: str) -> bool:
    """同步发送邮件"""
    try:
        msg = MIMEMultipart()
        msg['From'] = SMTP_USER
        msg['To'] = to_email
        msg['Subject'] = Header(subject, 'utf-8')
        msg.attach(MIMEText(body, 'html', 'utf-8'))

        server = smtplib.SMTP(SMTP_HOST, SMTP_PORT)
        server.starttls()
        server.login(SMTP_USER, SMTP_PASSWORD)
        server.send_message(msg)
        server.quit()

        print(f"✅ 邮件已发送到 {to_email}")
        return True
    except Exception as e:
        print(f"❌ 发送邮件失败: {e}")
        return False


def generate_code() -> str:
    """生成6位数字验证码"""
    return f"{random.randint(100000, 999999)}"


async def send_verification_code(db: Session, email: str, code_type: str) -> bool:
    """发送验证码并保存到数据库"""
    code = generate_code()

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

    # 打印到控制台（调试用）
    print(f"\n{'='*50}")
    print(f"📧 验证码: {code} -> {email}")
    print(f"{'='*50}\n")

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

    import asyncio
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, send_email_sync, email, subject, body)


def verify_code(db: Session, email: str, code: str, code_type: str) -> bool:
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
        db.commit()
        print(f"✅ 验证码验证成功: {email}")
        return True

    print(f"❌ 验证码验证失败: {email}, code={code}")
    return False