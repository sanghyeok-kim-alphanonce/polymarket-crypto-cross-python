FROM python:3.11-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir \
    asyncpg \
    redis \
    aiohttp

# 공유 패키지 복사
COPY packages/polymarket_common/ /app/packages/polymarket_common/
COPY packages/service_common/ /app/packages/service_common/
ENV PYTHONPATH="/app/packages"

COPY services/paper_trader_cross_limit_15m/ /app/

CMD ["python", "-u", "main.py"]
