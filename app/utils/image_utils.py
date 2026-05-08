"""
图像工具函数
- Base64 与 OpenCV 图像互转
- 图像缩放等
"""

import base64
import cv2
import numpy as np
from typing import Optional


def decode_base64_to_image(base64_str: str) -> Optional[np.ndarray]:
    """将 Base64 字符串解码为 OpenCV 图像（BGR格式）"""
    try:
        if ',' in base64_str:
            base64_str = base64_str.split(',')[1]
        img_data = base64.b64decode(base64_str)
        nparr = np.frombuffer(img_data, np.uint8)
        img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
        return img
    except Exception as e:
        print(f"Base64 解码失败: {e}")
        return None


def encode_image_to_base64(image: np.ndarray, format: str = ".jpg") -> str:
    """将 OpenCV 图像编码为 Base64 字符串"""
    _, buffer = cv2.imencode(format, image)
    return base64.b64encode(buffer).decode()


def read_image_from_path(image_path: str) -> Optional[np.ndarray]:
    """从文件路径读取图像"""
    image = cv2.imread(image_path)
    if image is None:
        print(f"无法读取图像: {image_path}")
        return None
    return image


def resize_image(image: np.ndarray, max_size: int = 1024) -> np.ndarray:
    """等比例缩放图像，限制最大边长"""
    h, w = image.shape[:2]
    if max(h, w) <= max_size:
        return image

    scale = max_size / max(h, w)
    new_w = int(w * scale)
    new_h = int(h * scale)
    return cv2.resize(image, (new_w, new_h))