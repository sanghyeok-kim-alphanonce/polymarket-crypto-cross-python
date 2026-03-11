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
COPY packages/service_common/ /app/packages/service_common/
ENV PYTHONPATH="/app/packages"

COPY services/binance_ws/ /app/

CMD ["python", "-u", "main.py"]
