FROM python:3.11-slim

WORKDIR /app

RUN pip install --no-cache-dir \
    fastapi \
    uvicorn[standard] \
    asyncpg

# 공유 패키지 복사
COPY packages/strategy_config/ /app/packages/strategy_config/
ENV PYTHONPATH="/app/packages"

# Copy strategies from paper_trader
COPY services/paper_trader/strategies/ /app/strategies/

# Copy crossing strategies
COPY services/paper_trader_cross_v2/strategies/crossing_v2.py /app/strategies/
COPY services/paper_trader_cross/strategies/crossing.py /app/strategies/

# Copy backtest_api code
COPY services/backtest_api/config.py /app/
COPY services/backtest_api/runner.py /app/
COPY services/backtest_api/main.py /app/

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
