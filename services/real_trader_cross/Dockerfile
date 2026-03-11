FROM python:3.11-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir \
    asyncpg \
    redis \
    aiohttp \
    py-clob-client

# 공유 패키지 복사
COPY packages/polymarket_common/ /app/packages/polymarket_common/
COPY packages/service_common/ /app/packages/service_common/
ENV PYTHONPATH="/app/packages"

COPY services/real_trader_cross/ /app/

CMD ["python", "-u", "main.py"]
