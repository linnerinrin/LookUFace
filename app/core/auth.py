"""
认证核心模块
app/core/auth.py
verify_password 验证密码是否正确
get_password_hash 把字符串密码转化为哈希值
create_access_token 创建/登录账户时生成一个jwt
decode_token 解码jwt
"""

from datetime import datetime, timedelta
from jose import JWTError, jwt
from passlib.context import CryptContext
from app.config import settings

pwd_context = CryptContext(schemes=["pbkdf2_sha256"], deprecated="auto")

SECRET_KEY = settings.SECRET_KEY
ALGORITHM = settings.ALGORITHM
ACCESS_TOKEN_EXPIRE_MINUTES = settings.ACCESS_TOKEN_EXPIRE_MINUTES


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """验证密码"""
    return pwd_context.verify(plain_password, hashed_password)


def get_password_hash(password: str) -> str:
    """生成密码哈希"""
    return pwd_context.hash(password)


def create_access_token(data: dict) -> str:
    """创建 JWT token"""
    to_encode = data.copy() #防止污染
    expire = datetime.utcnow() + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES) #过期时间 utcnow 表示utc时区时间 防止不同地区服务器时间不一致
    to_encode.update({"exp": expire}) #增加一个过期时间(jwt标准key)
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM) #用 payload  密钥 算法进行编码


def decode_token(token: str) -> dict:
    """解码 JWT token"""
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        return payload
    except JWTError:
        return None