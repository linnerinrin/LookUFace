# ============================================================================
# LookUFace —— 多阶段构建
#
# 相对旧 Dockerfile 的改进：
#   1. 依赖声明统一到 pyproject.toml（旧版只读 requirements.txt，
#      而 requirements.txt 已改为指向 pyproject 的兼容层）；
#   2. 先只拷贝依赖清单再装依赖，改代码不会让依赖层失效（构建缓存利用）；
#   3. 以非 root 用户运行（旧版是 root）；
#   4. 显式创建全部运行时目录，并声明 VOLUME 便于挂载；
#   5. 健康检查直接用 python 标准库，不额外装 curl。
# ============================================================================

FROM python:3.11-slim AS base

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    TZ=Asia/Shanghai

# OpenCV / MediaPipe 的运行时系统依赖
#   libgl1        -> cv2 需要
#   libglib2.0-0  -> cv2 / mediapipe 需要
#   libgomp1      -> OpenMP，Caffe 模型推理需要
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        libgl1 \
        libglib2.0-0 \
        libgomp1 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# ---------------------------------------------------------------------------
# 依赖层（只在依赖变化时重建）
# ---------------------------------------------------------------------------
FROM base AS deps

COPY pyproject.toml README.md ./
# 仅安装运行期依赖（不装 dev extras）
RUN pip install --upgrade pip setuptools wheel \
    && pip install .

# ---------------------------------------------------------------------------
# 运行层
# ---------------------------------------------------------------------------
FROM base AS runtime

COPY --from=deps /usr/local/lib/python3.11/site-packages /usr/local/lib/python3.11/site-packages
COPY --from=deps /usr/local/bin /usr/local/bin

# 只拷贝应用代码，不拷贝模型/日志/数据库（由 volume 或构建上下文控制）
COPY app/ ./app/
COPY pyproject.toml README.md ./

# 非 root 运行
RUN groupadd --system --gid 1001 lookuface \
    && useradd --system --uid 1001 --gid lookuface --create-home lookuface \
    && mkdir -p /app/logs /app/storage/screenshots /app/storage/qrcodes \
                /app/uploads/temp /app/models \
    && chown -R lookuface:lookuface /app

USER lookuface

# 容器内端口固定为 8000（下面的 CMD 显式传入，不依赖 .env）。
# 对外暴露的宿主机端口由 docker-compose.yml 的 ports 决定（当前 8835:8000）。
EXPOSE 8000

VOLUME ["/app/storage", "/app/logs", "/app/models"]

HEALTHCHECK --interval=30s --timeout=10s --start-period=40s --retries=3 \
    CMD python -c "import urllib.request,sys; \
r=urllib.request.urlopen('http://127.0.0.1:8000/api/v1/health', timeout=8); \
sys.exit(0 if r.status==200 else 1)" || exit 1

# 使用模块入口，保证 __main__ 里的浏览器打开逻辑不会在容器中触发（--no-browser）
CMD ["python", "-m", "app", "--host", "0.0.0.0", "--port", "8000", "--no-browser"]
