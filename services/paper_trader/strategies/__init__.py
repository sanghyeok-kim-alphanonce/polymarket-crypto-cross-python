"""
Strategies Package
"""
from .base import BaseStrategy, TradeSignal
from .v12_5 import V12_5Strategy, V12_5Config
from .v12_6 import V12_6Strategy, V12_6Config

# Crossing strategies (optional import - may not exist in paper_trader context)
try:
    from .crossing import CrossingStrategy, CrossingConfig
    from .crossing_v2 import CrossingV2Strategy, CrossingV2Config
    _has_crossing = True
except ImportError:
    _has_crossing = False

# Strategy Registry
STRATEGIES = {
    'v12_5': V12_5Strategy,
    'v12_6': V12_6Strategy,
}

if _has_crossing:
    STRATEGIES['crossing'] = CrossingStrategy
    STRATEGIES['crossing_v2'] = CrossingV2Strategy


def get_strategy(name: str, **kwargs) -> BaseStrategy:
    """전략 인스턴스 생성"""
    if name not in STRATEGIES:
        raise ValueError(f"Unknown strategy: {name}. Available: {list(STRATEGIES.keys())}")

    strategy_class = STRATEGIES[name]

    if name == 'v12_5':
        cfg = V12_5Config(**kwargs) if kwargs else None
        return strategy_class(cfg)

    if name == 'v12_6':
        cfg = V12_6Config(**kwargs) if kwargs else None
        return strategy_class(cfg)

    if name == 'crossing' and _has_crossing:
        cfg = CrossingConfig(**kwargs) if kwargs else None
        return strategy_class(cfg)

    if name == 'crossing_v2' and _has_crossing:
        cfg = CrossingV2Config(**kwargs) if kwargs else None
        return strategy_class(cfg)

    return strategy_class()


__all__ = [
    'BaseStrategy',
    'TradeSignal',
    'V12_5Strategy',
    'V12_5Config',
    'V12_6Strategy',
    'V12_6Config',
    'STRATEGIES',
    'get_strategy',
]
