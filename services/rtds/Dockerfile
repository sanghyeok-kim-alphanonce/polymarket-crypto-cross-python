FROM python:3.11-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir \
    asyncpg \
    redis \
    aiohttp \
    websockets

# 공유 패키지 복사
COPY packages/polymarket_common/ /app/packages/polymarket_common/
COPY packages/orderbook_shared/ /app/packages/orderbook_shared/
COPY packages/service_common/ /app/packages/service_common/
ENV PYTHONPATH="/app/packages"

COPY services/rtds/ /app/

CMD ["python", "-u", "main.py"]
