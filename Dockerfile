# syntax=docker/dockerfile:1
# 多阶段构建：web-builder 编译 Next.js 前端，api / web 两个运行镜像由 compose 分别取用。
# 部署步骤见 docs/deploy/docker-compose.md。

########## 阶段 1：前端构建 ##########
FROM node:22-alpine AS web-builder
ENV NEXT_TELEMETRY_DISABLED=1
WORKDIR /app/web
# 先只拷依赖清单，锁文件不变时命中缓存
COPY web/package.json web/package-lock.json ./
RUN npm ci
COPY web/ ./
# 前端把 /api 反代到后端，目标地址在 next build 时烘焙进产物，
# 必须与 compose 里的 api 服务名一致；改服务名时这里和 web 阶段要同步改
ARG PRICE_WEB_API_URL=http://api:8437
ENV PRICE_WEB_API_URL=$PRICE_WEB_API_URL
RUN npm run build

########## 阶段 2：API 运行镜像 ##########
FROM python:3.12-slim AS api
ENV TZ=Asia/Shanghai PYTHONUNBUFFERED=1
RUN apt-get update \
    && apt-get install -y --no-install-recommends tzdata \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /usr/local/bin/
# 先装依赖再拷源码：uv.lock 不变时依赖层命中缓存
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project
COPY llm_price_monitor/ ./llm_price_monitor/
COPY config/ ./config/
# 管理面板「使用文档」页在运行时读取仓库根 README.md，镜像里必须带上
COPY README.md ./
RUN uv sync --frozen --no-dev
# 无头浏览器采集：浏览器二进制与系统依赖烤进镜像，启动时只需自检不再联网下载
RUN playwright install --with-deps chromium
ENV PATH="/app/.venv/bin:$PATH"
EXPOSE 8437
CMD ["price-web", "--host", "0.0.0.0", "--port", "8437"]

########## 阶段 3：Web 运行镜像 ##########
FROM node:22-alpine AS web
ENV NODE_ENV=production TZ=Asia/Shanghai NEXT_TELEMETRY_DISABLED=1 PRICE_WEB_API_URL=http://api:8437
RUN apk add --no-cache tzdata
WORKDIR /app/web
COPY --from=web-builder /app/web/.next ./.next
COPY --from=web-builder /app/web/node_modules ./node_modules
COPY --from=web-builder /app/web/package.json ./
COPY --from=web-builder /app/web/next.config.ts ./
COPY --from=web-builder /app/web/public ./public
EXPOSE 3000
CMD ["npm", "run", "start"]
