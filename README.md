# LookUFace

基于人脸识别的场地计时缴费系统。摄像头帧经 WebSocket 推送，服务端完成
**人脸检测 → 性别年龄估计 → 身份识别 → 签到/暂离/签退与计费**。

> 本文档对应 v1.1.0（全量重构后）。重构前的缺陷清单与修复说明见
> [§7 重构说明](#7-重构说明)。

---

## 1. 快速开始

### 本地运行

```bat
:: 1. 准备配置
copy .env.example .env
::    编辑 .env，至少填写 SECRET_KEY
::    生成密钥：python -c "import secrets; print(secrets.token_urlsafe(64))"

:: 2. 一键启动（自动建虚拟环境、装依赖、校验配置）
start.bat
```

手动方式：

```bash
python -m venv venv
venv\Scripts\activate
pip install -e ".[dev]"
python -m app                 # 会尝试自动打开浏览器
python -m app --no-browser    # 不打开浏览器
```

访问 <http://localhost:8000/static/idx.html>，API 文档在 <http://localhost:8000/docs>。

### 容器运行

```bash
cp .env.example .env      # 填好 SECRET_KEY
docker compose up -d --build
```

### 运行测试

```bash
pytest                     # Python：单元 + 集成 + 契约 + 真实浏览器冒烟
node --test tests/frontend # 前端：api.js 单元测试
```

---

## 2. 目录结构

```
app/
├── main.py              FastAPI 应用工厂 + 生命周期（后台任务）
├── __main__.py          命令行入口（python -m app）
├── config.py            配置（绝对路径 env_file、全字段默认值、弱密钥校验）
├── logger.py            日志（UTF-8、线程安全 WebSocket 广播、请求链路 ID)
├── api/
│   ├── router.py        /api/v1 路由聚合
│   ├── deps.py          认证依赖
│   ├── schemas.py       统一请求/响应模型
│   ├── ws.py            WebSocket（摄像头流 / 日志流）
│   └── routes/          auth, faces, sessions, billing, logs, health
├── core/
│   ├── errors.py        领域异常
│   ├── errors_handlers.py  异常 → HTTP 响应
│   └── middleware.py    请求 ID、访问日志、安全响应头、体积限制
├── db/
│   ├── models.py        引擎/会话/ORM（表结构与旧版完全一致）
│   └── repository.py    所有数据访问
├── domain/
│   ├── session.py       计费领域逻辑（纯函数，可完全单测）
│   └── registry.py      会话运行时状态（暂离起止）
├── services/
│   ├── analysis.py      检测+性别年龄+识别编排（含结果缓存）
│   ├── auth_service.py  注册/登录/改密
│   ├── email_service.py 验证码签发与校验
│   ├── frame_pipeline.py 视频帧处理 + 业务联动（含背压）
│   ├── qrcode_service.py 计费二维码
│   ├── security.py      密码哈希与 JWT
│   ├── session.py       签到/暂离/签退服务（含去抖与幂等）
│   └── storage.py       图像校验与安全落盘
├── static/              前端（api.js / camera-core.js 为公共模块）
└── vision/
    ├── base.py          线程安全模型基座 + 全局推理锁
    ├── detection.py     OpenCV DNN 人脸检测
    ├── demographics.py  性别年龄（Caffe）
    └── face_identity.py 特征提取与比对（算法核心）
tests/                   后端、契约与真实浏览器测试
```

---

## 3. 配置

全部配置项见 `.env.example`。几个关键项：

| 变量 | 说明 | 默认 |
|---|---|---|
| `SECRET_KEY` | **必填**。JWT 签名密钥，弱值会被拒绝启动 | — |
| `ALLOWED_ORIGINS` | CORS 白名单，逗号分隔 | 仅本机 |
| `FACE_CONF_THRESHOLD` | 人脸检测置信度。OpenCV 推荐 0.5 | 0.5 |
| `RECOGNIZE_THRESHOLD` | 识别相似度阈值，**需用真实数据标定** | 0.75 |
| `CHECKIN_DEBOUNCE_SECONDS` | 同一张脸重复签到的去抖窗口 | 5 |
| `AWAY_TIMEOUT_MINUTES` | 暂离超时，超时自动签退结算 | 30 |
| `BILLING_RATE_PER_MINUTE` | 计费单价（元/分钟） | 0.5 |
| `MAX_SESSION_MINUTES` | 单次会话计费上限，防天价账单 | 1440 |

> **关于 `RECOGNIZE_THRESHOLD`**：默认值是在「旧实现的 0.925 完全不可用」
> 基础上保守下调得出的，**不是**在真实人脸数据上标定出来的。
> 上线前请按 `tests/test_face_identity.py::test_threshold_calibration_report`
> 描述的方法，用真实样本采集同人/异人两条分布并取等错误率点。

---

## 4. API

所有接口位于 `/api/v1`。错误响应统一为：

```json
{ "detail": "人类可读说明", "code": "机器可读标识", "request_id": "8位ID", "details": {"字段": "说明"} }
```

### 认证 `/auth`

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/auth/send-code` | 发送邮箱验证码（`type`: `register` / `reset`） |
| POST | `/auth/register` | 注册并直接返回 token |
| POST | `/auth/login` | 登录 |
| POST | `/auth/reset-password` | 通过验证码重置密码 |
| GET/PUT | `/auth/profile` | 读取 / 更新资料 |
| POST | `/auth/change-password` | 修改密码 |

### 人脸库 `/faces`

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/faces` | 当前用户的全部人脸（含 `features_current` 标记） |
| POST | `/faces/register` | 注册/更新人脸（multipart，字段 `file` + `name`） |
| POST | `/faces/screenshot` | 取回注册截图 |
| PUT | `/faces/{name}` | 重命名 / 修正注册时间 |
| DELETE | `/faces/{name}` | 删除人脸及截图 |
| POST | `/faces/analyze` | 单张图片完整分析（调试/集成） |

### 计时会话 `/sessions`

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/sessions/checkin` | 签到；已是暂离则视为归位 |
| POST | `/sessions/checkout` | `away=true` 暂离 / `away=false` 签退结算 |
| POST | `/sessions/status` | 实时状态 + 预估分钟数 |
| POST | `/sessions/settle-expired` | 手动触发超时收口扫描 |

### 计费 `/billing`

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/billing/quote` | **只传人脸名**，分钟数与金额由服务端按会话计算 |
| GET | `/billing/qrcode/{filename}` | 取回二维码图片 |

### WebSocket `/ws`

| 路径 | 说明 |
|---|---|
| `/ws/camera?token=<JWT>` | 入口摄像头：识别到已知人脸**自动签到** |
| `/ws/camera-exit?token=<JWT>` | 出口摄像头：在线者自动暂离，暂离者自动签退 |
| `/ws/logs` | 控制台实时日志 |

帧以二进制 JPEG 发送；文本 `ping` 用于心跳，`stats` 获取处理统计。
返回示例：

```json
{
  "success": true, "role": "entry", "face_count": 1,
  "faces": [{ "bbox": [120,80,260,240], "gender": "Male", "age": "(25-32)",
              "confidence": 0.91, "identity": "张三",
              "identity_confidence": 0.97, "detection_confidence": 0.99 }],
  "processing_ms": 42.5,
  "actions": ["张三 已开始计时，离场或暂离请记得签退付费"]
}
```

---

## 5. 计费模型

**所有金额与时长均由服务端计算**，客户端无法指定。

```
计费时长 = clamp( (now − checkin_time) − 暂离总时长 , 0 , MAX_SESSION_MINUTES )
金额     = floor(计费时长分钟数) × BILLING_RATE_PER_MINUTE   （四舍五入到分）
```

关键设计：

* `user_faces.checkin_time` 存的是**本次会话的计费起点**，暂离期间**不推进**；
* 进行中的暂离时长由 `app/domain/registry.py` 的运行时状态提供；
* 不足一分钟不计费，避免「进门一秒算一分钟」；
* 时钟回拨时计费为 0 而**不是负数**（时长自动 clamp）；
* 进程重启会丢失「当前暂离从何时开始」，此时保守地把暂离起点回退到
  计费起点 —— **只会少计费，绝不会多计费**；
* 后台任务每 60 秒扫描一次，把暂离超时的会话自动签退结算。

> **表结构约束**：为满足「不引入迁移工具、表结构不变」的要求，
> `user_faces` 仍只有 `is_online / is_away / checkin_time / online_time`
> 四个状态列。需要第三、第四个状态位（暂离起点、已结算暂离秒数）的信息
> 放在进程内注册表里。**单进程部署**下语义完整；若将来要多进程或多实例，
> 需把 `SessionRegistry` 换成 Redis 实现（接口不变）。

---

## 6. 人脸识别算法

```
关键点(468×3) → 相似变换归一化（去质心 + 除以 RMS 距离）→ 距离矩阵(468×468) → 归一化到 [0,1]
相似度 = 1 − mean(|D₁ − D₂|)
```

* **相似变换归一化**消除平移与缩放，使同一张脸在不同距离下得到一致表示；
* **距离矩阵**对旋转、平移、缩放不变，对表情等局部形变平滑鲁棒；
* 特征以 `b"v2|" + JSON` 存储，**不使用 pickle**。从数据库反序列化 pickle
  字节流等于执行任意代码，旧记录会被标记 `features_current: false`
  并在管理页提示「需重新注册」，而不是被反序列化。

识别后端抽象为 `IdentityEmbedder` 协议，切换到 InsightFace 等深度嵌入模型
只需实现该协议并在 `app/vision/face_identity.py::get_embedder()` 中替换。

---

## 7. 重构说明

从 v1.0.x 全量重构到 v1.1.0，修复的主要缺陷：

### 正确性（P0）

| 缺陷 | 影响 | 修复 |
|---|---|---|
| 暂离把 `checkin_time` 往未来推 `away_time` 分钟 | 实测签退算出 **−25 分钟**，`online_time` 被扣减可为负 | 暂离不再修改计费起点；结算时从已流逝时间中扣除 |
| 特征只去中心化、不归一化尺度 | 同一人距离变化即失配；不同人相似度挤在 0.9+，0.925 阈值几乎无可用工作点 | 相似变换归一化 + 距离矩阵，同/异样本出现明确间隔 |
| 前端每帧（约 10fps）对每张脸各发一次签到/状态/签退请求 | 单人使用约 20~30 次 HTTP + 数据库写入/秒 | 业务判定移交服务端，前端零业务请求；服务端加去抖与幂等 |
| 共享 `cv2.dnn.Net` 与 `FaceMesh` 被 4 个工作线程并发调用 | 结果错乱甚至崩溃（两者都非线程安全） | 全局推理锁 + 线程安全单例 |
| `/check/pay` 前端从未调用、且要求客户端传时长 | 支付二维码功能实际是死的，且可免单 | 新增 `/billing/quote` 并接入管理页，分钟数由服务端计算 |
| 暂离超时无兜底 | 用户断线后 `is_online` 永远为 True，计费无限累计 | 后台任务按 `AWAY_TIMEOUT_MINUTES` 自动签退结算 |

### 安全（P1）

| 缺陷 | 修复 |
|---|---|
| 二维码文件名直接拼接用户输入（实测 `../../../evil` 可穿越目录） | 文件名由 UUID 生成，用户输入仅经白名单净化后作可读后缀；落盘前做目录越界断言 |
| `SECRET_KEY` 为弱值且硬编码在 `.env` | 已轮换；配置层拒绝含弱值标记或长度不足的密钥，并给出生成命令 |
| 计费/签退只依赖前端传来的姓名 | 金额与时长全部由服务端按数据库时间戳裁决 |
| 摄像头 WebSocket 完全不认证 | 握手校验 JWT，失败以 1008 关闭 |
| 管理页把用户名拼进 `onclick="f('...')"` | 事件委托 + `data-*` 属性，用户数据不进入可执行上下文 |
| 用 pickle 反序列化数据库里的特征 | 改为 JSON 格式；旧 pickle 记录只标记不反序列化 |
| 静态目录挂在 `/` | 改挂 `/static`，拼错的 API 路径返回 JSON 404 而不是 HTML |

### 工程与体验

* 所有包补上 `__init__.py`（此前项目**一个都没有**，依赖隐式命名空间包）；
* `env_file` 改为绝对路径 —— 此前从项目根目录之外启动必然抛 17 个配置错误；
* 日志中文乱码修复（强制 stdout 为 UTF-8）；
* 工作线程中的日志不再静默丢失（广播器持有主事件循环，跨线程投递）；
* 移除前端对 `console.log/warn/error` 的劫持（此前所有调试输出都灌进服务端日志）；
* 管理页每帧两条调试输出已删除；
* 调用链上残留的 `alert()` 改为页内提示条；
* 依赖统一到 `pyproject.toml`，移除与 `passlib 1.7.4` 不兼容的 `bcrypt`；
* Docker 改为多阶段构建、非 root 运行、模型不再被 `.dockerignore` 与挂载策略冲突；
* `start.bat` 不再误杀占用端口的进程；
* 补齐测试：计费、算法、会话、注册表、API 契约、前后端路径一致性、
  顶层声明冲突、以及**真实 Chrome 无头浏览器**冒烟测试。

### 已知限制

1. `RECOGNIZE_THRESHOLD` 未经真实人脸数据标定（见 §3）。
2. 会话运行时状态在进程内存中，多进程/多实例部署需替换为 Redis。
3. 支付仅为演示：`PAYMENT_GATEWAY_URL` 指向示例地址，未接入真实支付。
4. 无活体检测，照片对着镜头即可签到。
5. 未接入 Alembic，改表结构需手工处理。
