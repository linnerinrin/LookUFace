<<<<<<< HEAD
```markdown
# LookUFace - 人脸识别系统

> 基于 FastAPI 的实时人脸分析平台，支持人脸检测、身份识别、性别年龄估计，含完整用户体系。

---

## 核心流程

### 1. 用户认证

```
┌─────────────────────────────────────────────────────────────┐
│  登录                                                        │
│  showLoginModal() → 弹登录框                                  │
│    └─ 点登录 → handleLogin()                                 │
│         └─ POST /auth/login                                  │
│              └─ DB 查邮箱                                     │
│              └─ verify_password 验密码                         │
│              └─ JWT 签发 token                                │
│         └─ localStorage 存 token + user                       │
│         └─ 页面状态更新                                       │
└─────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────┐
│  注册                                                        │
│  showRegisterForm() → 弹注册框                                │
│    └─ 点获取验证码 → sendRegisterCode()                       │
│         └─ POST /auth/send-code                              │
│              └─ SMTP 发送验证码邮件                            │
│    └─ 点注册 → handleRegister()                              │
│         └─ POST /auth/register                               │
│              └─ 校验验证码                                    │
│              └─ 密码加密写入 DB                               │
│         └─ 自动登录                                          │
└─────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────┐
│  找回密码（流程同注册）                                        │
│  showForgotForm() → sendResetCode() → handleResetPassword()   │
│         └─ POST /auth/reset-password                         │
│              └─ 校验验证码 → 新密码加密写入 DB                  │
└─────────────────────────────────────────────────────────────┘
```

### 2. 实时人脸识别

```
┌─────────────────────────────────────────────────────────────┐
│  connect()                                                   │
│    ├─ startCamera()    打开浏览器摄像头                        │
│    ├─ new WebSocket   与后端 ws 握手                          │
│    └─ ws.onopen → startCaptureLoop()                        │
│         └─ 每 100ms 调用 captureAndSend()                    │
│              ├─ 从 video 抓帧到 canvas                        │
│              ├─ 水平翻转                                      │
│              ├─ toBlob 压缩 JPEG                              │
│              └─ ws.send(blob) ──────────────────────┐        │
│                                                      │        │
│  ┌───────────────────────────────────────────────────┘        │
│  │  后端 websocket_camera()                                   │
│  │    ├─ cv2.imdecode 解码图片                                │
│  │    ├─ run_sync_analysis() → 线程池                         │
│  │    │    └─ face_service.analyze()                         │
│  │    │         ├─ detector.detect()         找脸在哪          │
│  │    │         ├─ gender_age.predict()      性别年龄          │
│  │    │         └─ face_identity.recognize()  是谁            │
│  │    │              └─ MediaPipe 468关键点 + 余弦相似度      │
│  │    │              └─ 遍历 DB 已注册人脸比对                │
│  │    └─ websocket.send_json(result) ────────────┐           │
│  └────────────────────────────────────────────────┘           │
│                                                      │        │
│  ws.onmessage ←─────────────────────────────────────┘        │
│    └─ updateResults(data)                                     │
│         ├─ drawFaces()   画框（绿框熟人/红框陌生人）           │
│         └─ 渲染结果卡片（身份、性别、年龄、置信度）             │
└─────────────────────────────────────────────────────────────┘
```

### 3. 人脸注册

```
┌─────────────────────────────────────────────────────────────┐
│  registerCurrentFace()                                       │
│    ├─ 校验：单人脸 + 已登录 + 已开始识别                       │
│    ├─ 从 video 截帧 → canvas.toBlob → FormData               │
│    └─ POST /face/register                                    │
│         ├─ cv2.flip 翻转恢复原图                              │
│         ├─ 保存截图到 storage/screenshots/                    │
│         └─ face_identity.register()                           │
│              ├─ MediaPipe 提取 468 关键点                     │
│              ├─ 展平为特征向量                                │
│              └─ 存入 DB（绑 user_id）                         │
└─────────────────────────────────────────────────────────────┘
```

### 4. 人脸库管理

```
┌─────────────────────────────────────────────────────────────┐
│  获取列表                                                    │
│  loadFaces() → GET /face/list_with_screenshots               │
│    └─ DB 查 user_id 的人脸                                   │
│    └─ 渲染卡片：名字 + 注册时间 + 头像 + 预览/删除按钮         │
└─────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────┐
│  删除人脸                                                    │
│  deleteFace(name) → DELETE /face/{name}                      │
│    └─ DB 删记录 + 删截图文件                                  │
│    └─ 刷新列表                                               │
└─────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────┐
│  预览截图                                                    │
│  previewFace(name) → GET /face/screenshot/{name}             │
│    └─ 返回截图文件 → 弹窗大图展示                             │
└─────────────────────────────────────────────────────────────┘
```

### 5. 视频录制

```
┌─────────────────────────────────────────────────────────────┐
│  toggleRecording()                                           │
│    ├─ startRecording()                                       │
│    │    └─ new MediaRecorder(stream)                         │
│    │         └─ 每秒存一个 chunk                             │
│    └─ stopRecording()                                        │
│         └─ 合并 chunks → Blob → 浏览器下载 .webm 视频        │
└─────────────────────────────────────────────────────────────┘
```

---

## 技术栈

| 层 | 技术 |
|----|------|
| 后端 | FastAPI + SQLAlchemy + SQLite |
| 推理 | OpenCV DNN + Caffe + MediaPipe |
| 前端 | 原生 JS + Canvas + WebSocket |
| 认证 | JWT + passlib + SMTP 验证码 |
| 部署 | Docker + Nginx + HTTPS |

---

## 快速启动

```bash
# 双击 start.bat（Windows）
# 或
docker-compose up -d
```

浏览器打开 `http://localhost:8000/idx.html`。

---

## 目录结构

```
detector/
├── app/
│   ├── api/          # 人脸分析路由 + 数据模型
│   ├── auth/         # 认证路由 + 数据模型
│   ├── core/         # 检测器、年龄性别、身份识别
│   ├── services/     # 分析服务、任务服务、邮件服务
│   ├── static/       # 前端 HTML/JS/CSS
│   ├── config.py     # 配置（.env）
│   ├── database.py   # SQLAlchemy ORM
│   └── main.py       # FastAPI 入口
├── models/           # 预训练模型文件
├── storage/          # 截图 + 数据库
├── Dockerfile
├── docker-compose.yml
├── start.bat
└── requirements.txt
```
=======

| 方法 | 路径 | 功能 |
|------|------|------|
| POST | `/api/v1/auth/register` | 用户注册 |
| POST | `/api/v1/auth/login` | 用户登录 |
| POST | `/api/v1/detect/upload` | 图片人脸检测 |
| POST | `/api/v1/face/register` | 注册人脸 |
| GET | `/api/v1/face/list` | 获取人脸列表 |
| DELETE | `/api/v1/face/{name}` | 删除人脸 |
| WS | `/api/v1/ws/camera` | WebSocket 实时视频流 |
>>>>>>> 8131e18b27cc02a4a101ea9ce1548119699a05b8
