"""Crossing V2 strategies package."""
from .base import BaseStrategy, TradeSignal
from .crossing_v2 import CrossingV2Strategy, CrossingV2Config

__all__ = ['BaseStrategy', 'TradeSignal', 'CrossingV2Strategy', 'CrossingV2Config']
