FROM python:3.11-slim

WORKDIR /app

RUN pip install --no-cache-dir \
    asyncpg \
    redis \
    aiohttp \
    py-clob-client

# Patch py_clob_client http_helpers for configurable timeout
COPY services/real_trader_5m_improve/patches/http_helpers.py /usr/local/lib/python3.11/site-packages/py_clob_client/http_helpers/helpers.py

COPY packages/polymarket_common /app/packages/polymarket_common
COPY packages/service_common /app/packages/service_common
ENV PYTHONPATH=/app/packages

COPY services/real_trader_5m_improve/config.py .
COPY services/real_trader_5m_improve/main.py .

HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD test -f /tmp/healthy || exit 1

CMD ["python", "-u", "main.py"]
