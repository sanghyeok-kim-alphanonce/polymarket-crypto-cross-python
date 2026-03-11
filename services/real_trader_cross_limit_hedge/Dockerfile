FROM python:3.11-slim

WORKDIR /app

RUN pip install --no-cache-dir \
    asyncpg \
    redis \
    aiohttp \
    py-clob-client

COPY packages/polymarket_common /app/packages/polymarket_common
COPY packages/service_common /app/packages/service_common
ENV PYTHONPATH=/app/packages

COPY services/real_trader_cross_limit_hedge/config.py .
COPY services/real_trader_cross_limit_hedge/main.py .

HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD test -f /tmp/healthy || exit 1

CMD ["python", "-u", "main.py"]
