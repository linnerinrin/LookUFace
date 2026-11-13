# LookUFace

基于人脸识别的场地计时缴费系统。摄像头帧经 WebSocket 推送到服务端，完成
**人脸检测 → 性别年龄估计 → 身份识别 → 签到/暂离/签退与计费**，
最终生成支付二维码。当前版本 **v1.2.0**（单场地、无账号体系）。

---

## 1. 开始

### 手动流程（本机直跑）

```bash
# 1. 准备配置
cp .env.example .env          # Windows: copy .env.example .env
#    编辑 .env，至少填写 SECRET_KEY，生成方式：
python -c "import secrets; print(secrets.token_urlsafe(64))"

# 2. 建虚拟环境并安装依赖（依赖声明统一在 pyproject.toml）
python -m venv venv
venv\Scripts\activate         # Linux/macOS: source venv/bin/activate
pip install -e ".[dev]"       # 开发（含 pytest / ruff / mypy）；生产用 pip install .

# 3. 启动
python -m app                 # 启动并自动打开浏览器
python -m app --no-browser    # 不打开浏览器
```

Windows 下也可以直接跑 `start.bat`（自动建虚拟环境、装依赖、校验配置并启动）。

- 页面入口：<http://localhost:8835/static/idx.html>
- 交互式 API 文档：<http://localhost:8835/docs>（Swagger UI）
- 端口取自 `.env` 的 `PORT`（默认 `8835`）。改端口时**必须同步修改
  `ALLOWED_ORIGINS`**，否则浏览器按跨源拒绝请求（表现为 `Failed to fetch`）。

### Docker build 流程

```bash
# 1. 准备配置
cp .env.example .env          # 填好 SECRET_KEY

# 2. 构建镜像（多阶段构建，运行时以非 root 用户运行）
docker build -t lookuface:1.2.0 .

# 3a. 用 compose 一键起（推荐，端口/卷/健康检查/日志轮转都已编排好）
docker compose up -d --build

# 3b. 或纯 docker 手动起
docker run -d --name lookuface \
  -p 8835:8000 \
  --env-file .env \
  -v "$PWD/storage:/app/storage" \
  -v "$PWD/logs:/app/logs" \
  -v "$PWD/models:/app/models:ro" \
  lookuface:1.2.0
```

容器内固定监听 8000，compose 映射到宿主机 8835。访问地址与本机直跑相同。

```bash
docker compose logs -f        # 看日志
docker compose down           # 停止
docker compose ps             # 看健康状态（依赖 /api/v1/health）
```

### 测试

```bash
pytest                        # 后端：单元 + 集成 + API 契约 + 真实浏览器冒烟
node --test tests/frontend    # 前端 api.js 单元测试
```

---

## 2. 目录结构、核心文件与技术栈

```
detector/
├── app/
│   ├── main.py               FastAPI 应用工厂 + 生命周期（挂载路由/静态目录，启动后台超时结算任务）
│   ├── __main__.py           命令行入口（python -m app，可 --no-browser / --host / --port / --reload）
│   ├── config.py             全部配置（pydantic-settings，读 .env，弱密钥拒绝启动）
│   ├── logger.py             日志：UTF-8 控制台、按天轮转文件、有界队列 WebSocket 广播、请求链路 ID
│   ├── api/
│   │   ├── router.py         /api/v1 路由聚合
│   │   ├── deps.py           公共依赖（DB 会话注入等）
│   │   ├── schemas.py        统一请求/响应模型
│   │   ├── ws.py             WebSocket：入口/出口摄像头流、日志流（ping 心跳、stats 统计）
│   │   └── routes/
│   │       ├── faces.py      人脸库 CRUD、注册截图、单图分析
│   │       ├── sessions.py   签到 / 暂离 / 签退 / 状态 / 超时收口（限频）
│   │       ├── billing.py    计费报价、支付二维码下载（校验报价登记与有效期）
│   │       ├── site_settings.py  场地设置读写（单价、暂离超时等）
│   │       ├── logs.py       前端日志上报（控制字符转义）、历史日志尾部读取
│   │       └── health.py     健康检查（数据库、模型文件预检与模型加载就绪分开报告）、版本
│   ├── core/
│   │   ├── errors.py         领域异常
│   │   ├── errors_handlers.py 异常 → 统一 JSON 错误响应
│   │   └── middleware.py     请求 ID、访问日志、安全响应头、请求体流式字节限额（413）
│   ├── db/
│   │   ├── models.py         SQLAlchemy 引擎/会话/ORM + 幂等列迁移（away_since/away_seconds）
│   │   └── repository.py     全部数据访问（flush-only，事务归调用方；状态写支持 CAS 条件）
│   ├── domain/
│   │   ├── session.py        计费领域逻辑（纯函数：时长扣除暂离、上限 clamp、金额取整）
│   │   └── registry.py       会话运行时状态注册表（暂离起止，快照读、可改名迁移）
│   ├── services/
│   │   ├── analysis.py       检测+性别年龄+识别编排（结果缓存、世代号防跨代回填、TTL 批量清理）
│   │   ├── frame_pipeline.py 视频帧处理流水线（解码尺寸限额、信号量背压、出口 30s 迟滞区分暂离/离场）
│   │   ├── session.py        签到/暂离/签退服务（两阶段去抖、CAS 先库后内存、单事务结算、改名迁移）
│   │   ├── qrcode_service.py 支付二维码生成（24h 保留清理、报价登记表 + 有效期校验）
│   │   ├── settings_service.py 场地设置读写（记录真实落盘结果）
│   │   └── storage.py        图像校验与安全落盘（头部预检拒超大图、目录越界断言、路径回退解析）
│   ├── static/               前端（原生 HTML/JS/CSS，api.js / camera-core.js 为公共模块）
│   └── vision/
│       ├── base.py           线程安全模型基座（单例、全局推理锁、预检/加载就绪、reset 释放资源）
│       ├── detection.py      OpenCV DNN 人脸检测（Caffe）
│       ├── demographics.py   性别年龄估计（Caffe）
│       └── face_identity.py  特征提取与比对（MediaPipe 关键点→相似变换归一化→距离矩阵，版本化编码）
├── models/                   模型权重（face/age/gender 的 prototxt + 权重）
├── tests/                    后端单元/集成/契约测试、前端测试、真实浏览器冒烟
├── pyproject.toml            依赖与工具配置的唯一真源
├── Dockerfile                多阶段构建（非 root 运行、健康检查）
├── docker-compose.yml        编排（端口 8835:8000、卷、资源限制、日志轮转）
└── start.bat                 Windows 一键启动
```

### 技术栈

| 层 | 技术 |
|---|---|
| Web 框架 | FastAPI + Starlette + Uvicorn（ASGI） |
| 数据校验/配置 | Pydantic v2 / pydantic-settings |
| 数据库 | SQLite（默认）+ SQLAlchemy 2.x，启动时幂等列迁移 |
| 人脸检测 | OpenCV DNN（Caffe 人脸检测器） |
| 性别年龄 | OpenCV DNN（Caffe age/gender 网络） |
| 身份识别 | MediaPipe FaceMesh 468 关键点 → 相似变换归一化 → 距离矩阵相似度（`IdentityEmbedder` 协议可替换为 InsightFace 等） |
| 图像处理 | OpenCV / NumPy / Pillow |
| 二维码 | qrcode（PNG） |
| 前端 | 原生 HTML/JavaScript/CSS（无构建链），WebSocket 推帧 |
| 部署 | Docker 多阶段构建 + docker compose |
| 测试 | pytest（含 starlette TestClient 契约测试）、node:test（前端） |

---

## 3. API 列表

所有 REST 接口位于 `/api/v1` 前缀下。错误响应统一为：

```json
{ "detail": "人类可读说明", "code": "机器可读标识", "request_id": "8位ID", "details": {} }
```

### 系统（无前缀）

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/v1/health` | 健康检查：`database`、`models`（文件预检）与 `models_loaded`（推理实例加载就绪）分开报告 |
| GET | `/api/v1/version` | 应用版本 |

### 人脸库 `/api/v1/faces`

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/faces` | 列出全部已注册人脸（含 `features_current` 标记） |
| POST | `/faces/register` | 注册/更新人脸（multipart：`file` + `name`），失败时自动清理遗留截图 |
| POST | `/faces/screenshot` | 取回某人的注册截图 |
| PUT | `/faces/{name}` | 重命名（运行时状态随名迁移，不丢暂离起点）/ 修正注册时间 |
| DELETE | `/faces/{name}` | 删除人脸及其截图 |
| POST | `/faces/analyze` | 单张图片完整分析（检测+性别年龄+识别，调试用） |

### 计时会话 `/api/v1/sessions`

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/sessions/checkin` | 签到；已暂离则视为归位（去抖防重复） |
| POST | `/sessions/checkout` | `away=true` 暂离 / `away=false` 签退结算（支持补录签退时间） |
| POST | `/sessions/status` | 实时状态 + 预估计费分钟数 |
| POST | `/sessions/settle-expired` | 手动触发超时暂离批量签退（60 秒限频，被限频返回 `throttled`/`retry_after_seconds`） |

### 计费 `/api/v1/billing`

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/billing/quote` | 只传人脸名，分钟数与金额由服务端按会话裁决，生成支付二维码并登记报价 |
| GET | `/billing/qrcode/{filename}` | 下载二维码 PNG（校验报价登记与 15 分钟有效期，过期/未登记一律 404） |

### 场地设置 `/api/v1/settings`

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/settings` | 读取计费单价、暂离超时等设置（含真实落盘状态） |
| PUT | `/settings` | 更新设置 |

### 日志 `/api/v1/logs`

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/logs/frontend` | 前端日志上报（控制字符转义后入日志） |
| POST | `/logs/recent` | 查询历史日志（尾部读取，按级别/行数过滤，2MB 上限） |
| GET | `/logs/files` | 列出日志文件 |
| GET | `/logs/level` | 查询当前日志级别 |

### WebSocket `/api/v1/ws`

| 路径 | 说明 |
|---|---|
| `/ws/camera` | 入口摄像头：识别到已知人脸**自动签到** |
| `/ws/camera-exit` | 出口摄像头：在线者自动暂离；暂离满 30 秒出口迟滞后自动签退 |
| `/ws/logs` | 控制台实时日志推送（有界队列背压，满则丢弃并计数） |

帧以二进制 JPEG 发送；文本 `ping` 为心跳，`stats` 返回处理统计。返回示例：

```json
{
  "success": true, "role": "entry", "face_count": 1,
  "faces": [{ "bbox": [120,80,260,240], "gender": "Male", "age": "(25-32)",
              "identity": "张三", "identity_confidence": 0.97, "detection_confidence": 0.99 }],
  "processing_ms": 42.5,
  "actions": ["张三 已开始计时，离场或暂离请记得签退付费"]
}
```

---

## 4. 工作流

```
浏览器/摄像头页面
   │  二进制 JPEG 帧（WebSocket）
   ▼
ws.py（/ws/camera、/ws/camera-exit）
   │  ping/pong 心跳；stats 统计；先抢信号量再提交线程池（背压）
   ▼
frame_pipeline.py
   │  解码前限额（8MB/4096px/16MP）→ OpenCV 解码
   ▼
analysis.py（帧分析编排，结果缓存 + 世代号 + TTL 清理）
   ├── vision/detection.py     OpenCV DNN 人脸检测（bbox + 置信度）
   ├── vision/demographics.py  性别年龄估计
   └── vision/face_identity.py MediaPipe 关键点 → 归一化距离矩阵 → 与库内特征比对
   ▼
session.py（会话服务，业务裁决全在服务端）
   │  两阶段去抖 → CAS 写库（先数据库后内存）→ 单事务结算
   ├── 入口：未知脸忽略；已知脸未在线 → 签到；暂离中 → 归位
   ├── 出口：在线 → 暂离；暂离满 30s（出口迟滞）→ 签退结算
   └── 后台任务每 60s 扫描：暂离超过 AWAY_TIMEOUT_MINUTES → 自动签退
   ▼
domain/session.py（计费纯函数）
   │  计费时长 = clamp((结算时刻 − 计费起点) − 暂离总时长, 0, MAX_SESSION_MINUTES)
   │  金额     = floor(分钟数) × BILLING_RATE_PER_MINUTE
   ▼
billing.py + qrcode_service.py
   │  /billing/quote 按会话裁决金额 → 生成二维码（UUID 文件名）
   │  → 登记报价（15 分钟有效期）→ 前端轮询下载二维码完成支付
   ▼
db/repository.py（SQLite，flush-only + CAS）
   持久化：user_faces（is_online / is_away / checkin_time / online_time
          / away_since / away_seconds）
```

要点：

- **服务端裁决一切**：客户端只传帧和人脸名，时长与金额永远由服务端按数据库时间戳计算，无法免单；
- **先库后内存**：所有状态迁移先在数据库做 CAS（compare-and-set），成功后才发布到进程内注册表，去抖窗口也在落库成功后才确认，崩溃重启不会留下「内存以为已签退、库里还在计时」的分裂态；
- **暂离语义**：暂离期间计费不推进；出口摄像头区分「路过瞥一眼」（30 秒内归位不计离场）与真离场；
- **兜底链**：出口漏检 → 后台 60s 扫描超时自动签退；运维也可手动 `/sessions/settle-expired`（限频）；
- **可观测**：`/health` 区分模型文件预检与推理实例加载就绪；日志按天轮转（`app_YYYY-MM-DD.log`）、WebSocket 实时推送（有界队列）、请求全链路带 `request_id`。
