FROM python:3.11-slim

WORKDIR /app

# 의존성 설치
RUN pip install --no-cache-dir redis aiohttp

# 소스 복사
COPY . .

CMD ["python", "-u", "main.py"]
