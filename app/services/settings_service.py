# """模块：场地设置服务"""
# """架构作用：更新进程内配置，并用临时文件替换环境配置文件"""
"""场地设置服务：在线调整业务参数并持久化到 ``.env``

本模块提供两件事：

1. **运行时生效**：直接改 ``settings`` 这个单例上的字段。所有业务代码都是
   ``settings.BILLING_RATE_PER_MINUTE`` 现取现用，因此立刻生效、无需重启
2. **落盘**：把改动写回 ``.env``，重启后仍然保留。写入是「保留注释与键顺序、
   只替换目标行」的定点改写，然后 ``os.replace`` 原子替换，不会写坏整个文件

安全提示：这些值会直接影响计费。因为系统已无身份校验，
只有可信环境才应开放该接口 —— ``HOST`` 默认 ``127.0.0.1`` 即是这层前提
"""

from __future__ import annotations

import os
import threading
from pathlib import Path
from typing import Any

from app.config import settings
from app.logger import logger

#: 设置字段名 -> 环境变量名。顺序即页面展示顺序
FIELD_TO_ENV: dict[str, str] = {
    "billing_rate_per_minute": "BILLING_RATE_PER_MINUTE",
    "away_timeout_minutes": "AWAY_TIMEOUT_MINUTES",
    "max_session_minutes": "MAX_SESSION_MINUTES",
    "checkin_debounce_seconds": "CHECKIN_DEBOUNCE_SECONDS",
    "checkout_debounce_seconds": "CHECKOUT_DEBOUNCE_SECONDS",
    "recognize_threshold": "RECOGNIZE_THRESHOLD",
    "face_conf_threshold": "FACE_CONF_THRESHOLD",
}

#: 整型字段（其余按float处理），用于确定落盘时的格式
_INT_FIELDS = {
    "away_timeout_minutes",
    "max_session_minutes",
    "checkin_debounce_seconds",
    "checkout_debounce_seconds",
}


# 将有限的业务字段转换为环境变量文本；浮点数按六位小数写入
def _format_value(field: str, value: Any) -> str:
    if field in _INT_FIELDS:
        return str(int(value))
    # 0.5而不是0.500000；同时保证1.0写成1.0而非1
    text = f"{float(value):.6f}".rstrip("0").rstrip(".")
    return text or "0.0"


class SiteSettingsService:
    """读取 / 更新 / 持久化场地设置"""

    # 构造时定位 .env并缓存配置单例引用；同时记录最近一次落盘结果
    def __init__(self) -> None:
        self._lock = threading.Lock()
        #: 最近一次写入结果：None=尚未写入过；True/False=上次 _persist_to_env是否成功
        self._last_persist_ok: bool | None = None
        #: 最近一次落盘失败的原因（成功时清空）
        self._last_persist_error: str | None = None

    # ------------------------------------------------------------------ #
    # 路径
    # ------------------------------------------------------------------ #
    # 返回当前 .env路径，供响应与持久化使用
    @property
    def env_path(self) -> Path:
        """与 ``Settings`` 读取的同一个文件（可用 ``LOOKUFACE_ENV_FILE`` 覆盖）"""
        return settings.env_file_path

    # ------------------------------------------------------------------ #
    # 读
    # ------------------------------------------------------------------ #
    # 读取当前运行时设置，组装设置响应所需字段
    def snapshot(self) -> dict[str, Any]:
        """当前生效值"""
        return {field: getattr(settings, env_key) for field, env_key in FIELD_TO_ENV.items()}

    # ------------------------------------------------------------------ #
    # 写
    # ------------------------------------------------------------------ #
    # 先改运行时单例再写 .env，返回生效值及持久化是否成功
    def update(self, changes: dict[str, Any]) -> tuple[dict[str, Any], bool]:
        """应用一批改动：先改运行时，再落盘

        只有调用方显式传入（非 ``None``）的字段会被改动
        返回 ``(当前生效值, 是否成功落盘)``
        """
        cleaned = {k: v for k, v in changes.items() if v is not None and k in FIELD_TO_ENV}
        if not cleaned:
            return self.snapshot(), self.persisted_state()

        with self._lock:
            for field, value in cleaned.items():
                env_key = FIELD_TO_ENV[field]
                # pydantic BaseSettings允许赋值；业务代码每次现取，立刻生效
                object.__setattr__(settings, env_key, value)
                logger.info("场地设置已更新 %s=%s（运行时生效）", env_key, value)

            try:
                self._persist_to_env(cleaned)
                persisted = True
                self._last_persist_ok = True
                self._last_persist_error = None
            except OSError as exc:  # 只读目录 / 权限不足
                logger.error("场地设置落盘失败（运行时值已生效，重启会丢失）：%s", exc)
                persisted = False
                self._last_persist_ok = False
                self._last_persist_error = str(exc)

        return self.snapshot(), persisted

    # 读取现有 .env，覆盖业务字段后原子替换写回
    def _persist_to_env(self, changes: dict[str, Any]) -> None:
        """把改动写回 ``.env``，保留注释与键顺序"""
        path = self.env_path
        lines: list[str] = []
        if path.exists():
            lines = path.read_text(encoding="utf-8").splitlines()

        pending = {FIELD_TO_ENV[field]: _format_value(field, value) for field, value in changes.items()}

        written: set[str] = set()
        for index, line in enumerate(lines):
            stripped = line.strip()
            if not stripped or stripped.startswith("#") or "=" not in stripped:
                continue
            key = stripped.split("=", 1)[0].strip()
            if key in pending:
                lines[index] = f"{key}={pending[key]}"
                written.add(key)

        # .env里原本没有的键追加到末尾
        missing = [f"{key}={value}" for key, value in pending.items() if key not in written]
        if missing:
            if lines and lines[-1].strip():
                lines.append("")
            lines.append("# ---- 场地设置（由设置页在线写入）----")
            lines.extend(missing)

        payload = "\n".join(lines).rstrip("\n") + "\n"

        # 原子替换：先写临时文件再rename，避免中途异常把 .env写坏
        temp_path = path.with_name(path.name + ".tmp")
        temp_path.write_text(payload, encoding="utf-8")
        os.replace(temp_path, path)
        logger.info("场地设置已写入 %s（%s）", path, ", ".join(sorted(pending)))

    # ------------------------------------------------------------------ #
    # 运维辅助
    # ------------------------------------------------------------------ #
    # 已发生过写入时报告上次写入的真实结果；否则退回到可写性探测
    def persisted_state(self) -> bool:
        """当前设置是否已成功持久化（决定页面上要不要提示「重启后会丢失」）

        写入过则以最后一次 ``_persist_to_env`` 的结果为准；
        尚未写入过时探测 ``.env`` 可写性作为预估
        """
        if self._last_persist_ok is not None:
            return self._last_persist_ok
        path = self.env_path
        try:
            if path.exists():
                return os.access(path, os.W_OK)
            return os.access(path.parent, os.W_OK)
        except OSError:
            return False

    # 最近一次落盘失败的错误信息（无失败时为None），供页面展示
    @property
    def last_persist_error(self) -> str | None:
        return self._last_persist_error


site_settings_service = SiteSettingsService()

__all__ = ["FIELD_TO_ENV", "SiteSettingsService", "site_settings_service"]
