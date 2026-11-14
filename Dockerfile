# 文件职责：构建TicketPilot后端运行镜像。
# 作用与配合：包含后端、MCP子进程和迁移代码，供后续Compose使用。
# 边界：不复制本机虚拟环境、密钥或业务数据，不在构建时连接数据库。

# 使用明确版本的Python和Debian基础环境。
FROM python:3.12.16-slim-bookworm

# 从官方uv镜像中只复制uv程序。
COPY --from=ghcr.io/astral-sh/uv:0.11.32 /uv /usr/local/bin/uv

# 所有后续相对路径都以/app为基础。
WORKDIR /app

# 日志及时输出；运行时不生成pyc文件。
# 使用基础镜像的Python，不额外下载解释器。
# PATH让python等命令优先使用容器自己的虚拟环境。
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    UV_PYTHON_DOWNLOADS=0 \
    UV_LINK_MODE=copy \
    PATH="/app/.venv/bin:$PATH"

# 安装OCR/OpenCV和ONNX相关Linux运行库。
# 安装完成后清理软件包索引，减少镜像中的缓存。
# 默认使用官方HTTPS源；遇到特定网络DNS问题时，仅本次构建可切换镜像。
ARG DEBIAN_MIRROR=https://deb.debian.org/debian
ARG DEBIAN_SECURITY_MIRROR=https://security.debian.org/debian-security
RUN sed -i "s|http://deb.debian.org/debian-security|${DEBIAN_SECURITY_MIRROR}|g; s|http://deb.debian.org/debian|${DEBIAN_MIRROR}|g" /etc/apt/sources.list.d/debian.sources \
    && apt-get -o Acquire::Retries=2 -o Acquire::https::Timeout=20 update \
    && apt-get -o Acquire::Retries=2 -o Acquire::https::Timeout=20 install -y --no-install-recommends \
        libgl1 \
        libglib2.0-0 \
        libgomp1 \
    && rm -rf /var/lib/apt/lists/*

# 先复制依赖清单，便于复用依赖安装层。
COPY pyproject.toml uv.lock ./

# 按锁文件安装运行依赖。
# 项目源码在下一步复制，因此这里不安装项目本身。
RUN uv sync --locked --no-dev --no-install-project --no-cache

# 只复制运行后端和数据库迁移所需的代码。
COPY ticketpilot ./ticketpilot
COPY migrations ./migrations
COPY alembic.ini ./alembic.ini

# 创建普通运行用户，并准备可写的数据目录。
RUN groupadd --gid 10001 ticketpilot \
    && useradd --uid 10001 --gid 10001 --create-home ticketpilot \
    && mkdir -p /app/data \
    && chown -R ticketpilot:ticketpilot /app/data

# 后端使用普通用户运行。
USER ticketpilot

# 声明容器内后端监听的端口。
EXPOSE 8000

# 使用单worker，与当前工作流的进程内互斥设计一致。
CMD ["python", "-m", "uvicorn", "ticketpilot.app:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
