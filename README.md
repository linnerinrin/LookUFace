# LookUFace

基于人脸识别的场地进出场打卡系统。摄像头帧经 WebSocket 推送到服务端，完成
**人脸检测 → 身份识别 → 自动签到 / 签退**，每次进出场写一条流水。
当前版本 **v1.2.0**（单场地、无账号体系）。

> 本版本已移除：控制台日志页与自建日志系统、年龄/性别识别、金额与收款、暂离与去抖。
> 只保留「谁在场、何时进、何时出、待了多久」；进程输出交给 uvicorn 自带的日志。

---

## 1. 开始

### 手动流程（本机直跑）

```bash
# 1. 准备配置
cp .env.example .env          # Windows: copy .env.example .env

# 2. 建虚拟环境并安装依赖（依赖声明统一在 pyproject.toml）
python -m venv venv
venv\Scripts\activate         # Linux/macOS: source venv/bin/activate
pip install -e ".[dev]"       # 开发（含 pytest / ruff / mypy）；生产用 pip install .
```

`models/` 被 `.gitignore` 忽略，权重不随仓库分发。首次运行前执行
`python -m app.download_models` 下载 OpenCV 人脸检测器两项即可（不再是六项）。

```bash
# 3. 启动
python -m app                 # 启动并自动打开浏览器
python -m app --no-browser    # 不打开浏览器
```

Windows 下也可以直接跑 `start.bat`。

- 页面入口：<http://localhost:8835/static/idx.html>
- 交互式 API 文档：<http://localhost:8835/docs>（Swagger UI）
- 端口取自 `.env` 的 `PORT`（默认 `8835`）。改端口时**必须同步修改
  `ALLOWED_ORIGINS`**，否则浏览器按跨源拒绝请求（表现为 `Failed to fetch`）。

### 页面

| 页面 | 文件 | 作用 |
|---|---|---|
| 实时识别 | `static/idx.html` | 入口/出口两路摄像头并排，识别即打卡 |
| 仓库管理 | `static/manager.html` | 人脸库增删改、手动签到/签退、导出 CSV |
| 数据看板 | `static/dashboard.html` | 上方补录进出场数据；主体为 KPI + 五张多维图表（见下） |

数据看板主体由服务端用 matplotlib 渲染成 PNG（异步生成，离线可用、不依赖前端图表库），
包含：用户时长/次数排行、近 N 日趋势（可切 7/30 天）、到场时段分布、
单次时长分布、用户 × 日期热力图。图表数据来自 `session_records` 流水表。
| 场地设置 | `static/settings.html` | 在线调整识别阈值并写回 `.env` |

四个页面共用 `api.js`（请求封装 / 提示 / 主题）与 `manager.css` 的主题变量；
深色模式在任意一页切换后，其余页面通过 `localStorage` + `storage` 事件同步。

### 测试

```bash
pytest            # 全量：API 契约、会话流水、看板与主题、死功能守门
```

---

## 2. 目录结构

```
detector/
├── app/
│   ├── main.py               应用工厂 + 生命周期（挂载路由/静态目录）
│   ├── __main__.py           命令行入口（python -m app）
│   ├── config.py             全部配置（pydantic-settings，读 .env）
│   ├── download_models.py    补齐缺失的人脸检测器权重
│   ├── api/
│   │   ├── router.py         /api/v1 路由聚合
│   │   ├── deps.py           公共依赖（DB 会话注入）
│   │   ├── schemas.py        统一请求/响应模型
│   │   ├── ws.py             摄像头 WebSocket（ping 心跳、stats 统计）
│   │   └── routes/
│   │       ├── faces.py      人脸库 CRUD、注册截图、单图分析
│   │       ├── sessions.py   签到 / 签退 / 状态
│   │       ├── site_settings.py  场地设置读写
│   │       └── health.py     健康检查、版本
│   ├── core/
│   │   ├── errors.py         领域异常
│   │   ├── errors_handlers.py 异常 → 统一 JSON 错误响应
│   │   └── middleware.py     请求 ID、安全响应头、请求体限额
│   ├── db/
│   │   ├── models.py         ORM + 幂等迁移（user_faces / session_records）
│   │   └── repository.py     全部数据访问（flush-only，事务归调用方；状态写支持 CAS）
│   ├── domain/
│   │   └── session.py        会话领域快照与时长计算（纯函数）
│   ├── services/
│   │   ├── analysis.py       检测 + 身份识别编排（快照缓存、世代号、TTL）
│   │   ├── frame_pipeline.py 视频帧流水线（解码限额、信号量背压、进出场裁决）
│   │   ├── session.py        签到/签退服务（CAS 先库后内存、单事务、流水写入）
│   │   ├── settings_service.py 场地设置读写（记录真实落盘结果）
│   │   └── storage.py        图像校验与安全落盘
│   ├── static/               前端（原生 HTML/JS/CSS，api.js / camera-core.js 为公共模块）
│   └── vision/
│       ├── base.py           线程安全模型基座（单例、推理锁、预检/加载就绪）
│       ├── detection.py      OpenCV DNN 人脸检测
│       └── face_identity.py  特征提取与比对（MediaPipe 关键点 → 归一化 → 距离矩阵，版本化编码）
├── models/                   opencv_face_detector 的 prototxt + 权重
├── tests/                    契约 / 会话流水 / 前端与主题 / 死功能守门
├── pyproject.toml            依赖与工具配置的唯一真源
├── start.bat                 Windows 一键启动（建 venv、装依赖、起服务）
└── README.md
```

### 技术栈

| 层 | 技术 |
|---|---|
| Web 框架 | FastAPI + Starlette + Uvicorn（ASGI） |
| 数据校验/配置 | Pydantic v2 / pydantic-settings |
| 数据库 | SQLite（默认）+ SQLAlchemy 2.x，启动时幂等迁移 |
| 人脸检测 | OpenCV DNN（Caffe 人脸检测器） |
| 身份识别 | MediaPipe FaceMesh 468 关键点 → 相似变换归一化 → 距离矩阵相似度 |
| 图像处理 | OpenCV / NumPy |
| 数据看板图表 | matplotlib（服务端异步渲染 PNG，中文字体自动探测） |
| 前端 | 原生 HTML/JavaScript/CSS（无构建链），WebSocket 推帧 |
| 部署 | 本机直跑（`python -m app` 或 `start.bat`） |
| 测试 | pytest（含 starlette TestClient 契约测试） |

---

## 3. API 列表

所有 REST 接口位于 `/api/v1` 前缀下。错误响应统一为：

```json
{ "detail": "人类可读说明", "code": "机器可读标识", "request_id": "8位ID", "details": {} }
```

### 系统

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/v1/health` | 健康检查：`database`、`models`（文件预检）与 `models_loaded`（推理实例就绪）分开报告 |
| GET | `/api/v1/version` | 应用版本与阈值 |

### 人脸库 `/api/v1/faces`

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/faces` | 列出全部已注册人脸（含 `features_current` 标记） |
| POST | `/faces/register` | 注册/更新人脸（multipart：`file` + `name`），失败时自动清理遗留截图 |
| POST | `/faces/screenshot` | 取回某人的注册截图 |
| PUT | `/faces/{name}` | 重命名（运行态随名迁移）/ 修正注册时间 |
| DELETE | `/faces/{name}` | 删除人脸及其截图 |
| POST | `/faces/analyze` | 单张图片完整分析（检测 + 身份，调试用） |

### 打卡会话 `/api/v1/sessions`

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/sessions/checkin` | 进场打卡；已在场则返回 `deduplicated=true`，不重复记账 |
| POST | `/sessions/checkout` | 出场打卡，支持补录 `checkout_time` / `duration_minutes` |
| POST | `/sessions/status` | 实时状态（是否在场、已待分钟数） |

### 数据看板 `/api/v1/records` `/api/v1/stats`

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/records` | 补录一段完整进出场区间（只写流水，不改当前在场状态） |
| GET | `/records` | 流水列表，支持 `?name=` 与 `?limit=` |
| GET | `/stats/overview?days=7` | KPI + 五张图表（PNG data URI），`days` 取值 1–90 |

### 场地设置 `/api/v1/settings`

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/settings` | 读取识别阈值（含真实落盘状态） |
| PUT | `/settings` | 局部更新，立即生效并写回 `.env` |

### WebSocket `/api/v1/ws`

| 路径 | 说明 |
|---|---|
| `/ws/camera` | 入口摄像头：识别到在场者以外的人 **自动签到** |
| `/ws/camera-exit` | 出口摄像头：识别到在场者 **自动签退** |

帧以二进制 JPEG 发送；文本 `ping` 为心跳，`stats` 返回处理统计。返回示例：

```json
{
  "success": true, "role": "entry", "face_count": 1,
  "faces": [{ "bbox": [120,80,260,240], "identity": "张三",
              "identity_confidence": 0.97, "detection_confidence": 0.99 }],
  "processing_ms": 42.5,
  "actions": ["张三 签到成功"]
}
```

---

## 4. 数据模型

一个 SQLite 文件 `storage/face_lens.db`，两张表：

| 表 | 作用 |
|---|---|
| `user_faces` | **当前状态**：谁在人脸库里、是否在场、本次签到时刻、累计在场分钟数 |
| `session_records` | **历史流水**：每次进场一条（`checkin_at` / `checkout_at` / `duration_seconds` / `duration_minutes` / `source`） |

`user_faces` 只存「现在」，签退即清空签到时间；流水表记录「发生过什么」，
供看板与导出使用。`source` 取值为 `entry` / `exit` / `manual`。

---

## 5. 工作流

```
浏览器摄像头页面
   │  二进制 JPEG 帧（WebSocket，约 6~7 fps）
   ▼
ws.py（/ws/camera、/ws/camera-exit）
   │  ping/pong 心跳；stats 统计；先抢信号量再提交线程池（背压）
   ▼
frame_pipeline.py
   │  解码前限额（8MB/4096px/16MP）→ OpenCV 解码
   ▼
analysis.py（帧分析编排，快照缓存 + 世代号）
   ├── vision/detection.py     OpenCV DNN 人脸检测（bbox + 置信度）
   └── vision/face_identity.py MediaPipe 关键点 → 归一化距离矩阵 → 与库内特征比对
   ▼
session.py（业务裁决全在服务端）
   ├── 入口：未知脸忽略；已知脸未在场 → 签到（写流水）
   └── 出口：在场 → 签退（闭合流水、累计时长）
   ▼
db/repository.py（SQLite，flush-only + CAS）
   user_faces（is_online / checkin_time / online_time）+ session_records
```

要点：

- **服务端裁决一切**：客户端只传帧和人脸名，进出场时刻与时长都由服务端按数据库时间戳计算；
- **先库后内存**：状态迁移先在数据库做 CAS，成功后才更新内存态，崩溃重启不会留下分裂态；
- **流水与状态同事务**：签到写「未闭合流水」，签退在同一事务里闭合并累计时长；
- **兜底**：升级前就在场的会话没有流水，签退时补写一条完整记录，不会丢账；
- **可观测**：`/health` 区分模型文件预检与推理实例就绪；每个响应带 `X-Request-ID` 与 `X-Process-Time-ms`。
