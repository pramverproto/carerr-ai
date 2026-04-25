FROM python:3.13-slim

# 配置国内 pip 镜像源（清华源在国内速度通常最快）
RUN pip config set global.index-url https://pypi.tuna.tsinghua.edu.cn/simple/ \
    && pip config set global.trusted-host pypi.tuna.tsinghua.edu.cn \
    && pip install uv --no-cache-dir

WORKDIR /app

# 先复制依赖文件，利用 Docker 层缓存
COPY pyproject.toml uv.lock ./
RUN UV_HTTP_TIMEOUT=300 uv sync --frozen --no-dev --no-install-project \
    --index-url https://pypi.tuna.tsinghua.edu.cn/simple/

# 复制源码和数据文件
COPY . .

EXPOSE 8000

CMD ["uv", "run", "--no-sync", "uvicorn", "api:app", "--host", "0.0.0.0", "--port", "8000"]
