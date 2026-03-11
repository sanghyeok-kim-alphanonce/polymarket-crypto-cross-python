"""
service_common - 서비스 공통 유틸리티

AsyncServiceBase: DB/Redis 연결, graceful shutdown, health check 공통 로직
setup_logging: 표준 로깅 설정
"""
import asyncio
import logging
import pathlib
import signal
import sys
import time
from typing import Optional

import asyncpg
import redis.asyncio as aioredis


def setup_logging(name: str, level: int = logging.INFO) -> logging.Logger:
    """표준 로깅 설정. 각 서비스 main.py 상단에서 호출."""
    sys.stdout.reconfigure(line_buffering=True)
    sys.stderr.reconfigure(line_buffering=True)
    logging.basicConfig(
        level=level,
        format='%(asctime)s [%(levelname)s] %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S',
        stream=sys.stdout,
    )
    return logging.getLogger(name)


class AsyncServiceBase:
    """
    비동기 서비스 공통 베이스 클래스.

    제공 기능:
    - DB 연결 (asyncpg pool, 10회 재시도)
    - Redis 연결 (redis.asyncio, ping 확인)
    - Graceful shutdown (signal handler + resource cleanup)
    - Health check file loop (Docker healthcheck용)

    사용법:
        class MyService(AsyncServiceBase):
            def __init__(self):
                super().__init__(DB_HOST, DB_PORT, DB_NAME, DB_USER, DB_PASSWORD,
                                 REDIS_HOST, REDIS_PORT)
            def is_healthy(self) -> bool:
                return super().is_healthy() and <서비스별 조건>
            async def on_shutdown(self):
                await self.flush_buffers()  # 서비스별 cleanup
    """

    def __init__(
        self,
        db_host: str, db_port: int, db_name: str, db_user: str, db_password: str,
        redis_host: str, redis_port: int,
    ):
        self._db_host = db_host
        self._db_port = db_port
        self._db_name = db_name
        self._db_user = db_user
        self._db_password = db_password
        self._redis_host = redis_host
        self._redis_port = redis_port

        self.db_pool: Optional[asyncpg.Pool] = None
        self.redis_client: Optional[aioredis.Redis] = None

    # =========================================================================
    # DB & Redis 연결
    # =========================================================================
    async def connect_db(self) -> bool:
        logger = logging.getLogger(__name__)
        for i in range(10):
            try:
                logger.info(f"DB connection attempt {i+1}/10...")
                self.db_pool = await asyncpg.create_pool(
                    host=self._db_host, port=self._db_port,
                    database=self._db_name, user=self._db_user,
                    password=self._db_password,
                    min_size=2, max_size=5,
                )
                logger.info(f"DB pool created: {self._db_host}:{self._db_port}/{self._db_name}")
                return True
            except Exception as e:
                logger.error(f"DB connection failed: {e}")
                if i < 9:
                    await asyncio.sleep(3)
        return False

    async def connect_redis(self) -> bool:
        logger = logging.getLogger(__name__)
        try:
            self.redis_client = aioredis.Redis(
                host=self._redis_host, port=self._redis_port,
                decode_responses=True,
            )
            await self.redis_client.ping()
            logger.info(f"Connected to Redis: {self._redis_host}:{self._redis_port}")
            return True
        except Exception as e:
            logger.warning(f"Redis connection failed: {e}")
            self.redis_client = None
            return False

    # =========================================================================
    # Graceful Shutdown & Health
    # =========================================================================
    async def on_shutdown(self):
        """서비스별 shutdown hook. 서브클래스에서 override하여 버퍼 flush 등 수행."""
        pass

    async def shutdown(self):
        logger = logging.getLogger(__name__)
        logger.info("[SHUTDOWN] Graceful shutdown starting...")
        try:
            await self.on_shutdown()
        except Exception as e:
            logger.error(f"[SHUTDOWN] on_shutdown error: {e}")
        if self.db_pool:
            await self.db_pool.close()
            logger.info("[SHUTDOWN] DB pool closed")
        if self.redis_client:
            await self.redis_client.close()
            logger.info("[SHUTDOWN] Redis closed")
        logger.info("[SHUTDOWN] Done")

    def is_healthy(self) -> bool:
        """Health check. 서브클래스에서 override 가능. 기본: db_pool 존재 확인."""
        return self.db_pool is not None

    def _handle_signal(self, loop: asyncio.AbstractEventLoop):
        logging.getLogger(__name__).info("[SIGNAL] Received shutdown signal")
        loop.create_task(self._signal_shutdown())

    async def _signal_shutdown(self):
        await self.shutdown()
        asyncio.get_event_loop().stop()

    def setup_signal_handlers(self):
        """SIGTERM/SIGINT handler 등록. run() 내에서 호출."""
        loop = asyncio.get_event_loop()
        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(sig, lambda: self._handle_signal(loop))

    async def _health_file_loop(self):
        """주기적으로 /tmp/healthy 파일 갱신 (Docker healthcheck용)"""
        health_file = pathlib.Path("/tmp/healthy")
        while True:
            try:
                if self.is_healthy():
                    health_file.write_text(str(time.time()))
                else:
                    health_file.unlink(missing_ok=True)
            except Exception:
                pass
            await asyncio.sleep(5)
