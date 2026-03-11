"""
Redis 기반 Orderbook 통신
orderbook_collector_ws (Writer) <---> paper_trader / real_trader (Readers)
"""
import json
import os
import redis
from typing import Dict, List, Optional
from dataclasses import dataclass


@dataclass
class OrderbookLevel:
    """호가 레벨"""
    price: float
    size: int

    def to_dict(self):
        return {"price": self.price, "size": self.size}

    @classmethod
    def from_dict(cls, data: Dict):
        return cls(price=data["price"], size=data["size"])


@dataclass
class OrderbookSnapshot:
    """Orderbook 스냅샷 (Depth 5)"""
    timestamp: float
    market_key: str  # "btc_15m_down"
    market_slug: str
    token_id: str

    best_bid: float
    best_ask: float

    # Depth 5
    asks: List[OrderbookLevel]  # 최대 5개
    bids: List[OrderbookLevel]  # 최대 5개

    def to_dict(self):
        return {
            "timestamp": self.timestamp,
            "market_key": self.market_key,
            "market_slug": self.market_slug,
            "token_id": self.token_id,
            "best_bid": self.best_bid,
            "best_ask": self.best_ask,
            "asks": [level.to_dict() for level in self.asks],
            "bids": [level.to_dict() for level in self.bids],
        }

    @classmethod
    def from_dict(cls, data: Dict):
        return cls(
            timestamp=data["timestamp"],
            market_key=data["market_key"],
            market_slug=data["market_slug"],
            token_id=data["token_id"],
            best_bid=data["best_bid"],
            best_ask=data["best_ask"],
            asks=[OrderbookLevel.from_dict(level) for level in data["asks"]],
            bids=[OrderbookLevel.from_dict(level) for level in data["bids"]],
        )


class OrderbookRedis:
    """
    Redis 기반 Orderbook 저장소

    장점:
    - 컨테이너 간 생명주기 독립적
    - 재시작 순서 무관
    - 자동 TTL로 stale data 방지
    """

    TTL_SECONDS = 60  # Orderbook 만료 시간 (60초)
    KEY_PREFIX = "orderbook:"

    def __init__(self, host: str = None, port: int = None):
        self.host = host or os.getenv("REDIS_HOST", "localhost")
        self.port = port or int(os.getenv("REDIS_PORT", 6379))
        self.redis_client = redis.Redis(
            host=self.host,
            port=self.port,
            db=0,
            decode_responses=True,
            socket_connect_timeout=5,
            socket_timeout=5,
        )

    def _get_key(self, market_key: str) -> str:
        return f"{self.KEY_PREFIX}{market_key}"

    def write_orderbook(self, snapshot: OrderbookSnapshot):
        """Orderbook 스냅샷 Redis에 쓰기"""
        key = self._get_key(snapshot.market_key)
        data = json.dumps(snapshot.to_dict())
        self.redis_client.setex(key, self.TTL_SECONDS, data)

    def read_orderbook(self, market_key: str) -> Optional[OrderbookSnapshot]:
        """Orderbook 스냅샷 Redis에서 읽기"""
        key = self._get_key(market_key)
        data = self.redis_client.get(key)

        if not data:
            return None

        try:
            snapshot_dict = json.loads(data)
            return OrderbookSnapshot.from_dict(snapshot_dict)
        except (json.JSONDecodeError, KeyError):
            return None

    def get_all_markets(self) -> List[str]:
        """현재 저장된 모든 market keys 조회"""
        pattern = f"{self.KEY_PREFIX}*"
        keys = self.redis_client.keys(pattern)
        return [key.replace(self.KEY_PREFIX, "") for key in keys]

    def delete_orderbook(self, market_key: str):
        key = self._get_key(market_key)
        self.redis_client.delete(key)

    def close(self):
        self.redis_client.close()

    def ping(self) -> bool:
        try:
            return self.redis_client.ping()
        except:
            return False

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()


# Utility functions
def get_orderbook(coin: str, timeframe: str, side: str) -> Optional[OrderbookSnapshot]:
    """
    편의 함수: 특정 market의 orderbook 조회

    Usage:
        ob = get_orderbook('btc', '15m', 'down')
        if ob:
            print(f"Best ask: {ob.best_ask}")
    """
    market_key = f"{coin}_{timeframe}_{side}"
    with OrderbookRedis() as redis_ob:
        return redis_ob.read_orderbook(market_key)
