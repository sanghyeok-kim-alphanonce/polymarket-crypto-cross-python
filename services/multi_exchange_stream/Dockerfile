FROM python:3.11-slim

WORKDIR /app

# ccxt includes ccxt.pro for WebSocket streaming
RUN pip install --no-cache-dir \
    ccxt \
    redis \
    psycopg2-binary

# Copy service code
COPY services/multi_exchange_stream/*.py ./

CMD ["python", "-u", "main.py"]
