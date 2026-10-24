"""Репозитории: весь доступ к базе данных, в хендлерах SQL не пишется."""

from bot.repositories.base import BaseRepo
from bot.repositories.chat import ChatRepo
from bot.repositories.content import ContentRepo
from bot.repositories.game import GameRepo, RoundAnswer, SessionTotal
from bot.repositories.member import MemberRepo
from bot.repositories.score import LeaderboardEntry, ScoreRepo
from bot.repositories.streak import StreakRepo
from bot.repositories.usage import UsageRepo

__all__ = [
    "BaseRepo",
    "ChatRepo",
    "ContentRepo",
    "GameRepo",
    "LeaderboardEntry",
    "MemberRepo",
    "RoundAnswer",
    "ScoreRepo",
    "SessionTotal",
    "StreakRepo",
    "UsageRepo",
]
