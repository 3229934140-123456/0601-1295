from .base import BaseDataCollector
from .email_collector import EmailDataCollector
from .im_collector import InstantMessageCollector
from .door_collector import DoorAccessCollector
from .finance_collector import FinanceDataCollector


def get_all_collectors() -> list[BaseDataCollector]:
    return [
        EmailDataCollector(),
        InstantMessageCollector(),
        DoorAccessCollector(),
        FinanceDataCollector(),
    ]


__all__ = [
    "BaseDataCollector",
    "EmailDataCollector",
    "InstantMessageCollector",
    "DoorAccessCollector",
    "FinanceDataCollector",
    "get_all_collectors",
]
