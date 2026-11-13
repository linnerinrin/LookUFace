# """架构作用：连接持久化人脸状态、内存暂离注册表与纯计费领域模型；统一承接人工与摄像头动作"""
# """模块：按人脸名串行处理签到、归位、暂离和结算，并向接口返回可核对的计费明细"""
"""在线会话服务：签到/暂离/签退 与计费结算

1. **暂离不推进签到时间**：暂离起止由 ``SessionRegistry`` 记录，结算时从已流逝时间中减去
2. **计费由服务端唯一裁决**：金额、分钟数全部由后端根据数据库时间戳计算，
   前端传上来的任何时长都只作为展示参考，不参与结算
3. **幂等与去抖**：同一张脸在 ``CHECKIN_DEBOUNCE_SECONDS`` 内的重复签到请求
   直接返回上一次结果，不重复写库
4. **并发安全**：同一张脸（``name``）的操作被细粒度锁串行化
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from app.config import settings
from app.core.errors import NotFoundError, ValidationError
from app.db.models import UserFace
from app.db.repository import get_face, online_faces, set_face_state
from app.domain.registry import session_registry
from app.domain.session import (
    BillingResult,
    Breakdown,
    SessionState,
    bill,
    breakdown,
    compute_amount,
    preview_minutes,
)
from app.logger import logger


@dataclass(slots=True)
class ActionResult:
    """一次业务动作的结果"""

    success: bool
    message: str
    name: str
    state: SessionState
    deduplicated: bool = False
    billing: BillingResult | None = None
    #: 本次动作是否真的发生了结算（暂离、重复请求都不会结算）
    settled: bool = False
    #: 计费算式明细，便于排查「为什么是0分钟」
    breakdown: Breakdown | None = None
    #: 本次会话的计费起点（结算后state里已清空，因此单独带出）
    checkin_at: datetime | None = None
    #: 本次结算**实际使用的时刻**
    #:
    #: 为什么不直接让前端用"现在"：用户可能自定义了签退时间（事后补签退），
    #: 也可能服务端把未来时刻收敛成了当前时刻。把真实使用的时刻回传，
    #: 前端才能把「签到 -> 签退 -> 计费」这条时间账原样展示出来，
    #: 用户自己就能核对"为什么是0分钟"，不必回来问
    settled_at: datetime | None = None


class _Debounce:
    """按key的短时去抖，抑制同帧/连帧重复请求

    key包含**人脸记录id**，并定期清理过期条目，避免字典无限增长
    """

    _PRUNE_THRESHOLD = 512

    # 单调时钟不受系统校时影响，锁保证查询与记录窗口是一次原子操作
    def __init__(self) -> None:
        self._last: dict[tuple[str, int], float] = {}
        self._lock = threading.Lock()

    # 未命中时立即占用窗口，调用方后续写库失败也不会自动撤销这次记录
    def should_skip(self, key: tuple[str, int], window_seconds: float) -> bool:
        if window_seconds <= 0:
            return False
        now = time.monotonic()
        with self._lock:
            if len(self._last) > self._PRUNE_THRESHOLD:
                self._prune_locked(now)
            previous = self._last.get(key)
            if previous is not None and (now - previous) < window_seconds:
                return True
            self._last[key] = now
            return False

    # 两阶段去抖：check只查不记，confirm在业务成功后确认窗口，失败不写库的请求不占窗口
    def check(self, key: tuple[str, int], window_seconds: float) -> bool:
        """仅检查窗口是否命中，不记录"""
        if window_seconds <= 0:
            return False
        now = time.monotonic()
        with self._lock:
            if len(self._last) > self._PRUNE_THRESHOLD:
                self._prune_locked(now)
            previous = self._last.get(key)
            return previous is not None and (now - previous) < window_seconds

    # 业务写库成功后确认窗口，避免失败请求白白占用窗口抑制后续合法请求
    def confirm(self, key: tuple[str, int]) -> None:
        with self._lock:
            self._last[key] = time.monotonic()

    # 仅在持有内部锁时调用，超过条目阈值才扫描；并非每次请求全表清理
    def _prune_locked(self, now: float) -> None:
        # 保留窗口取最大可能值，保守清理
        horizon = max(settings.CHECKIN_DEBOUNCE_SECONDS, settings.CHECKOUT_DEBOUNCE_SECONDS) * 4 + 60
        stale = [k for k, ts in self._last.items() if (now - ts) > horizon]
        for key in stale:
            self._last.pop(key, None)

    # 生命周期结束时按动作与记录id移除一个窗口，不影响同名的新记录
    def forget(self, key: tuple[str, int]) -> None:
        """删除某张脸的窗口（人脸被删除/重命名时调用）"""
        with self._lock:
            self._last.pop(key, None)

    # 运维重置全部去抖历史；此操作与具体会话状态无绑定
    def clear(self) -> None:
        with self._lock:
            self._last.clear()

    # 返回当前窗口条目数量，包含尚未达到惰性清理条件的旧记录
    def __len__(self) -> int:
        with self._lock:
            return len(self._last)


class SessionService:
    """场地计时会话服务"""

    # 人物锁映射只在本进程生效；数据库事务和多进程互斥不由这些锁提供
    def __init__(self) -> None:
        self._locks: dict[str, threading.Lock] = {}
        self._locks_guard = threading.Lock()
        self._debounce = _Debounce()

    # ------------------------------------------------------------------ #
    # 工具
    # ------------------------------------------------------------------ #
    # 映射锁只负责创建/获取人物锁，之后业务调用独占该人物，其他人可并行处理
    def _lock_for(self, name: str) -> threading.Lock:
        with self._locks_guard:
            lock = self._locks.get(name)
            if lock is None:
                lock = threading.Lock()
                self._locks[name] = lock
            return lock

    # 将仓储层的不存在结果转换为接口可识别的业务异常
    @staticmethod
    def _load_face(db: Session, name: str) -> UserFace:
        face = get_face(db, name)
        if face is None:
            raise NotFoundError(f"人脸不存在：{name}", code="face_not_found")
        return face

    # 从当前ORM对象取持久字段，再合并注册表暂离字段；本方法本身不重新查询数据库
    @staticmethod
    def state_of(
        name: str,
        face: UserFace,
        *,
        now: datetime | None = None,
    ) -> SessionState:
        return session_registry.build_state(
            name=name,
            is_online=bool(face.is_online),
            is_away=bool(face.is_away),
            checkin_at=face.checkin_time,
            accumulated_minutes=int(face.online_time or 0),
            away_since=face.away_since,
            away_seconds=face.away_seconds,
            now=now,
        )

    # ------------------------------------------------------------------ #
    # 签到
    # ------------------------------------------------------------------ #
    # 姓名归一后在人物锁内选择新会话、归位或幂等返回，减少重复摄像头写入
    def check_in(
        self,
        db: Session,
        *,
        name: str,
        now: datetime | None = None,
        skip_debounce: bool = False,
    ) -> ActionResult:
        """签到。已是暂离状态则视为归位"""
        moment = now or datetime.now()
        name = (name or "").strip()
        if not name:
            raise ValidationError("人名为空")

        with self._lock_for(name):
            face = self._load_face(db, name)

            # 去抖窗口只查不记，写库成功后才confirm；合法的「签退后立即再进场」
            # 不会被吞，因为 _finalize成功时会forget对应的in窗口
            if not skip_debounce and not face.is_online:
                if self._debounce.check(
                    ("in", face.id), settings.CHECKIN_DEBOUNCE_SECONDS
                ):
                    logger.debug("签到请求被去抖抑制 name=%s", name)
                    return ActionResult(
                        success=True,
                        message=f"{name} 签到请求已受理（去抖窗口内重复请求）",
                        name=name,
                        state=self.state_of(name, face, now=moment),
                        deduplicated=True,
                    )

            # 情况一：全新会话
            if not face.is_online:
                # 先写库（CAS防止并发重复签到），提交成功后才发布内存状态
                ok = set_face_state(
                    db,
                    face_id=face.id,
                    is_online=True,
                    is_away=False,
                    checkin_at=moment,
                    away_since=None,
                    away_seconds=0,
                    expected_online=False,
                )
                if not ok:
                    db.rollback()
                    db.refresh(face)
                    logger.warning("签到 CAS 未命中（并发已处理）name=%s", name)
                    return ActionResult(
                        success=True,
                        message=f"{name} 已在计时中",
                        name=name,
                        state=self.state_of(name, face, now=moment),
                        deduplicated=True,
                    )
                db.commit()
                session_registry.begin(name, now=moment)
                self._debounce.confirm(("in", face.id))
                face.is_online = True
                face.is_away = False
                face.checkin_time = moment
                face.away_since = None
                face.away_seconds = 0
                logger.info("签到成功（新会话）name=%s", name)
                return ActionResult(
                    success=True,
                    message=f"{name} 已开始计时，离场或暂离请记得签退付费",
                    name=name,
                    state=self.state_of(name, face, now=moment),
                )

            # 情况二：暂离归位
            if face.is_away:
                # 收口依据与重启恢复使用同一快照：内存缺失时由持久层away_since/away_seconds回退
                state = self.state_of(name, face, now=moment)
                ended = 0.0
                if state.away_started_at is not None:
                    ended = max(0.0, (moment - state.away_started_at).total_seconds())
                settled_after = state.settled_away_seconds + ended
                ok = set_face_state(
                    db,
                    face_id=face.id,
                    is_away=False,
                    away_since=None,
                    away_seconds=int(settled_after),
                    expected_away=True,
                )
                if not ok:
                    db.rollback()
                    db.refresh(face)
                    logger.warning("归位 CAS 未命中（并发已处理）name=%s", name)
                    return ActionResult(
                        success=True,
                        message=f"{name} 已归位，继续计时",
                        name=name,
                        state=self.state_of(name, face, now=moment),
                        deduplicated=True,
                    )
                db.commit()
                # 提交成功后同步内存注册表
                session_registry.clear_away(
                    name,
                    now=moment,
                    fallback_started_at=state.away_started_at,
                    fallback_settled_seconds=state.settled_away_seconds,
                )
                self._debounce.forget(("away", face.id))
                face.is_away = False
                face.away_since = None
                face.away_seconds = int(settled_after)
                logger.info(
                    "签到成功（暂离归位）name=%s 暂离 %d 秒", name, int(ended)
                )
                return ActionResult(
                    success=True,
                    message=f"{name} 已归位，继续计时",
                    name=name,
                    state=self.state_of(name, face, now=moment),
                )

            # 情况三：本来就在线
            logger.debug("重复签到被忽略 name=%s", name)
            return ActionResult(
                success=True,
                message=f"{name} 已在计时中",
                name=name,
                state=self.state_of(name, face, now=moment),
                deduplicated=True,
            )

    # ------------------------------------------------------------------ #
    # 签退 / 暂离
    # ------------------------------------------------------------------ #
    # 暂离只记状态；最终签退解析补录窗口，归一时刻后调用统一结算入口
    def check_out(
        self,
        db: Session,
        *,
        name: str,
        away: bool,
        now: datetime | None = None,
        skip_debounce: bool = False,
        checkout_time: datetime | None = None,
        checkin_time: datetime | None = None,
        duration_minutes: int | None = None,
    ) -> ActionResult:
        """签退（``away=False``）或标记暂离（``away=True``）

        补录在场时间有三种方式，优先级从高到低：

        1. ``duration_minutes`` —— **首选**。服务端把计费起点顺推为
           ``结算时刻 - duration_minutes``，所以"填600就是10小时"，
           永远算得出结果
        2. ``checkin_time`` —— 直接把起点改到指定时刻
        3. 都不给 —— 用现有的签到时间

        ``checkout_time`` 用于指定结算时刻，缺省为"现在"
        """
        moment = now or datetime.now()
        name = (name or "").strip()
        if not name:
            raise ValidationError("人名为空")

        # 缺省表示"现在"，但**只有** checkout_time走这个回落；
        # checkin_time的None必须保持None（否则会被误解成"起点=现在"）
        settle_at = moment if away else self._normalize_moment_required(checkout_time, moment)
        new_checkin = None if away else self._normalize_moment(checkin_time)

        with self._lock_for(name):
            face = self._load_face(db, name)

            if not face.is_online:
                return ActionResult(
                    success=False,
                    message=f"{name} 当前未在计时，无需签退",
                    name=name,
                    state=self.state_of(name, face, now=moment),
                )

            if away:
                return self._mark_away(db, face, name, moment, skip_debounce)

            # 结算时刻不能落在未来（未来时长无法结算），夹到当前时刻；
            # 但不报错 —— 用户填未来时刻的真实意图通常是"在场更久"，
            # 那应该通过duration_minutes表达
            if settle_at > moment:
                logger.info(
                    "签退时间 %s 晚于当前时刻 %s，已按当前时刻结算 name=%s"
                    "（如需更长的在场时间，请用「在场时长（分钟）」补录）",
                    settle_at.isoformat(sep=" "),
                    moment.isoformat(sep=" "),
                    name,
                )
                settle_at = moment

            # 计费起点：由「在场时长」或显式checkin_time决定，否则沿用现有签到时间
            override_checkin: datetime | None = None
            if duration_minutes is not None:
                override_checkin = settle_at - timedelta(minutes=max(0, int(duration_minutes)))
            elif new_checkin is not None:
                # 起点不能晚于结算时刻，否则会出现负时长
                override_checkin = min(new_checkin, settle_at)

            return self._finalize(
                db, face, name, settle_at,
                reason="manual", checkin_at=override_checkin,
            )

    # ------------------------------------------------------------------ #
    # 时刻归一化
    # ------------------------------------------------------------------ #
    # 有时区输入转换为本机本地时间后去掉时区，保持与数据库naive时间戳可比较
    @staticmethod
    def _normalize_moment(value: datetime | None) -> datetime | None:
        """把前端传来的时刻归一化成**与系统一致的无时区本地时间**

        带时区的值（如以 ``Z`` 结尾）先转成本地时间；``datetime-local`` 控件
        提交的值本身无时区，直接使用。``None`` 原样返回 ——
        调用方据此区分"用户没给"与"用户给了某个时刻"
        """
        if value is None:
            return None
        if value.tzinfo is not None:
            return value.astimezone().replace(tzinfo=None)
        return value

    # 只为有明确缺省语义的参数提供回退，避免把未填写的签到起点误改为当前时间
    @classmethod
    def _normalize_moment_required(
        cls,
        value: datetime | None,
        fallback: datetime,
    ) -> datetime:
        """归一化一个**可选但缺省有意义**的时刻（目前只用于签退时刻）

        缺省时回落到 ``fallback``（当前时刻）—— 因为"不填签退时间"
        确实就意味着"现在签退"。这与 ``checkin_time`` 的语义不同：
        不填签到时间意味着"不改动起点"，绝不能回落到now
        """
        normalized = cls._normalize_moment(value)
        return fallback if normalized is None else normalized

    # 已暂离直接返回；其余请求先去抖，再记内存起点和持久标志，签到起点保持原值
    def _mark_away(
        self,
        db: Session,
        face: UserFace,
        name: str,
        moment: datetime,
        skip_debounce: bool,
    ) -> ActionResult:
        if face.is_away:
            return ActionResult(
                success=True,
                message=f"{name} 已处于暂离状态",
                name=name,
                state=self.state_of(name, face, now=moment),
                deduplicated=True,
                checkin_at=face.checkin_time,
            )

        if not skip_debounce and self._debounce.check(
            ("away", face.id), settings.CHECKOUT_DEBOUNCE_SECONDS
        ):
            return ActionResult(
                success=True,
                message=f"{name} 暂离请求已受理（去抖窗口内重复请求）",
                name=name,
                state=self.state_of(name, face, now=moment),
                deduplicated=True,
                checkin_at=face.checkin_time,
            )

        # 先写库（CAS：仅当当前未暂离），提交成功后再发布内存起点；
        # 归位成功时已forget away窗口，合法往返不会被吞
        ok = set_face_state(
            db,
            face_id=face.id,
            is_away=True,
            away_since=moment,
            expected_away=False,
        )
        if not ok:
            db.rollback()
            db.refresh(face)
            return ActionResult(
                success=True,
                message=f"{name} 已处于暂离状态",
                name=name,
                state=self.state_of(name, face, now=moment),
                deduplicated=True,
                checkin_at=face.checkin_time,
            )
        db.commit()
        session_registry.mark_away(name, now=moment)
        self._debounce.confirm(("away", face.id))
        face.is_away = True
        face.away_since = moment
        logger.info(
            "暂离开始 name=%s（暂离不结算，签到时间保持 %s 不变）",
            name,
            face.checkin_time.isoformat(sep=" ") if face.checkin_time else "无",
        )
        return ActionResult(
            success=True,
            message=f"{name} 已暂离，超过 {settings.AWAY_TIMEOUT_MINUTES} 分钟未归将自动签退结算",
            name=name,
            state=self.state_of(name, face, now=moment),
            checkin_at=face.checkin_time,
        )

    # 从统一快照收口暂离，计算账单后落库总分钟并删除运行态；调用方负责互斥
    def _finalize(
        self,
        db: Session,
        face: UserFace,
        name: str,
        moment: datetime,
        *,
        reason: str = "normal",
        checkin_at: datetime | None = None,
    ) -> ActionResult:
        """结算并关闭会话。计费完全由服务端计算

        ``checkin_at`` 允许**覆盖本次的计费起点**（补录时长用），
        必须通过参数传入（``state_of`` 会重新读数据库，直接改内存对象的赋值会被忽略）
        """
        state = self.state_of(name, face, now=moment)
        if checkin_at is not None:
            # 补录语义 = 净计费时长：用户显式给出的起点/时长即最终要收费的口径，
            # 不再扣除历史暂离（历史暂离区间无法按窗口裁剪，沿用会少收费甚至归零）
            state = SessionState(
                is_online=state.is_online,
                is_away=False,
                checkin_at=checkin_at,
                accumulated_minutes=state.accumulated_minutes,
                away_started_at=None,
                settled_away_seconds=0.0,
            )
        elif state.is_away:
            # 先把仍在进行中的暂离收口：用同一快照折算（内存缺失时state已带
            # 持久层回退的away_started_at），避免「settled与进行中暂离」双扣或漏扣
            ended = 0.0
            if state.away_started_at is not None:
                ended = max(0.0, (moment - state.away_started_at).total_seconds())
            state = SessionState(
                is_online=True,
                is_away=False,
                checkin_at=state.checkin_at,
                accumulated_minutes=state.accumulated_minutes,
                away_started_at=None,
                settled_away_seconds=state.settled_away_seconds + ended,
            )

        result = bill(
            state,
            moment,
            rate_per_minute=settings.BILLING_RATE_PER_MINUTE,
            max_session_minutes=settings.MAX_SESSION_MINUTES,
        )
        detail = breakdown(
            state,
            moment,
            max_session_minutes=settings.MAX_SESSION_MINUTES,
        )

        # 关键：online_time只在结算时增加，且服务端保证其为非负数
        # 整个结算（清暂离 + 离线 + 累计分钟 + 清暂离时间轴）在同一个事务里提交，
        # 失败时由调用方rollback，内存注册表只在提交成功后发布
        total = max(0, int(result.total_minutes))
        ok = set_face_state(
            db,
            face_id=face.id,
            is_online=False,
            is_away=False,
            checkin_at=None,
            away_since=None,
            away_seconds=0,
            accumulated_minutes=total,
            expected_online=True,
        )
        if not ok:
            # 并发已把会话关掉：放弃本次结算，按已收口状态返回
            db.rollback()
            db.refresh(face)
            logger.warning("结算 CAS 未命中（会话已被并发关闭）name=%s", name)
            return ActionResult(
                success=True,
                message=f"{name} 会话已被并发结算",
                name=name,
                state=self.state_of(name, face, now=moment),
                deduplicated=True,
            )
        db.commit()
        db.expire(face)

        session_registry.drop(name)
        # 成功的签退使「签退后立即再进场」成为合法转换，清掉in窗口避免误吞
        self._debounce.forget(("in", face.id))
        self._debounce.forget(("away", face.id))

        face.is_online = False
        face.is_away = False
        face.checkin_time = None
        face.away_since = None
        face.away_seconds = 0
        face.online_time = total

        # 把算式明细写进日志。只说「本次0分钟」无法区分
        # 「确实只待了几十秒」与「时间基准算错了」，因此把每一项都打出来
        logger.info(
            "签退结算 name=%s 本次 %d 分钟 累计 %d 分钟 金额 %.2f 元 原因=%s%s"
            "｜算式：签到=%s 结算=%s 已流逝=%d分钟 暂离=%d分钟 计费秒数=%d",
            name,
            result.session_minutes,
            total,
            result.amount,
            reason,
            "（触发计费上限）" if result.capped else "",
            state.checkin_at.isoformat(sep=" ") if state.checkin_at else "无",
            moment.isoformat(sep=" "),
            detail.elapsed_minutes,
            detail.away_minutes,
            detail.billable_seconds,
        )
        return ActionResult(
            success=True,
            message=(
                f"{name} 签退成功，本次 {result.session_minutes} 分钟，"
                f"累计 {total} 分钟，应付 {result.amount:.2f} 元"
            ),
            name=name,
            state=SessionState(is_online=False, accumulated_minutes=total),
            billing=result,
            settled=True,
            breakdown=detail,
            checkin_at=state.checkin_at,
            # 回传真实结算时刻：前端据此展示完整时间账
            settled_at=moment,
        )

    # ------------------------------------------------------------------ #
    # 计费报价（不改变任何状态）
    # ------------------------------------------------------------------ #
    # 同一个moment贯穿明细和金额，返回报价快照，不预占结算也不写库
    def quote(
        self,
        db: Session,
        *,
        name: str,
        now: datetime | None = None,
    ) -> tuple[SessionState, int, Breakdown, float, bool]:
        """查询当前应付金额明细，**不改变任何状态**

        返回 ``(状态, 计费分钟, 算式明细, 金额, 是否有应付金额)``

        「是否有应付金额」= 正在计时 且 计费分钟 > 0
        未计时（离线）或不足一分钟时，没有应付金额，调用方不应给出付款入口
        """
        moment = now or datetime.now()
        # 报价也走人物锁并在锁内重读，保证数据库状态与注册表快照一致
        with self._lock_for(name):
            face = self._load_face(db, name)
            db.refresh(face)
            state = self.state_of(name, face, now=moment)

        if not state.is_online or state.checkin_at is None:
            empty = Breakdown(
                elapsed_minutes=0,
                elapsed_seconds=0,
                away_minutes=0,
                billable_minutes=0,
                capped=False,
                billable_seconds=0,
            )
            return state, 0, empty, 0.0, False

        detail = breakdown(
            state,
            moment,
            max_session_minutes=settings.MAX_SESSION_MINUTES,
        )
        minutes = detail.billable_minutes
        amount = compute_amount(minutes, settings.BILLING_RATE_PER_MINUTE)
        # [问题:P2报价并发快照] 本路径未取人物锁，先载入的数据库状态可能与随后已归位/结算的注册表不对应；注册表还分开读取可变字段，报价可能短暂不一致。建议锁内重读并复制一致快照
        payable = minutes > 0 and amount > 0
        return state, minutes, detail, amount, payable

    # ------------------------------------------------------------------ #
    # 查询
    # ------------------------------------------------------------------ #
    # 查询持久字段并合并进程内暂离状态，不会触发任何会话转换
    def get_status(
        self,
        db: Session,
        *,
        name: str,
        now: datetime | None = None,
    ) -> SessionState:
        face = self._load_face(db, name)
        return self.state_of(name, face, now=now)

    # 离线显示0，在线使用领域模型的同一非负/上限/舍入规则预估
    def current_minutes(
        self,
        db: Session,
        *,
        name: str,
        now: datetime | None = None,
    ) -> int:
        """当前会话的预估计费分钟数（不落库）"""
        moment = now or datetime.now()
        face = self._load_face(db, name)
        state = self.state_of(name, face, now=moment)
        if not state.is_online:
            return 0
        return preview_minutes(state, moment, max_session_minutes=settings.MAX_SESSION_MINUTES)

    # 遍历数据库在线集合，结合内存或保守恢复起点判断暂离超时，逐人尝试结算
    def auto_settle_expired(self, db: Session, *, now: datetime | None = None) -> int:
        """兜底任务：把暂离超时的会话自动签退结算，按 ``AWAY_TIMEOUT_MINUTES`` 收口"""
        moment = now or datetime.now()
        settled = 0

        # 逐人处理：人物锁内重读记录并再次判定超时，失败即回滚不影响其他人
        for candidate in list(online_faces(db)):
            try:
                with self._lock_for(candidate.name):
                    face = get_face(db, candidate.name)
                    if face is None or not face.is_online:
                        continue
                    state = self.state_of(face.name, face, now=moment)
                    if not session_registry.is_expired(state, moment):
                        continue
                    self._finalize(
                        db,
                        face,
                        face.name,
                        moment,
                        reason="暂离超时自动签退",
                    )
                    settled += 1
            except Exception:
                # 逐人事务：异常时回滚本人物，避免失败事务阻断后续人脸
                db.rollback()
                logger.exception("自动签退失败 name=%s", candidate.name)

        if settled:
            logger.info("兜底任务已自动结算 %d 个超时暂离会话", settled)
        return settled

    # 数据库全量有效姓名是清理依据，避免单次帧未识别就误删暂离历史
    def drop_stale_runtime(self, db: Session) -> int:
        """清理数据库中已删除人脸的运行态"""
        from app.db.repository import all_face_names

        return session_registry.prune_unknown(all_face_names(db))

    # 只清空事件抑制窗口，不改变数据库与暂离注册表
    def clear_debounce(self) -> None:
        self._debounce.clear()

    # 生命周期清理按姓名处理运行态与锁，按记录id处理去抖；改名请用rename_runtime迁移
    def forget_face(self, name: str, *, face_id: int | None = None) -> None:
        """人脸被删除时，清理它的运行态、去抖窗口与锁

        去抖窗口的键是人脸记录id（不是名字），因此调用方若还持有被删记录的
        ``face_id``，应一并传入；否则该窗口会在下次prune时自然过期
        """
        session_registry.drop(name)
        if face_id is not None:
            self._debounce.forget(("in", face_id))
            self._debounce.forget(("away", face_id))
        with self._locks_guard:
            self._locks.pop(name, None)

    # 改名迁移：运行态、人物锁平移到新名；去抖键是记录id（改名不变）无需处理
    def rename_runtime(self, old_name: str, new_name: str) -> None:
        """人脸改名时，把运行时状态与人物锁迁移到新名，历史暂离不丢失"""
        session_registry.rename(old_name, new_name)
        with self._locks_guard:
            lock = self._locks.pop(old_name, None)
            if lock is not None:
                self._locks[new_name] = lock

    # 直接清空单例内部字典，仅适合没有并发请求的受控重置阶段
    def reset(self) -> None:
        """清空全部运行时状态（仅测试与运维重启场景使用）"""
        session_registry._states.clear()
        self._debounce.clear()
        with self._locks_guard:
            self._locks.clear()


session_service = SessionService()

__all__ = ["ActionResult", "SessionService", "session_service"]
