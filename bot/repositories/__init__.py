"""Репозитории: весь доступ к базе данных, в хендлерах SQL не пишется."""

from bot.repositories.admin import AdminActionRepo
from bot.repositories.base import BaseRepo
from bot.repositories.chat import ChatRepo
from bot.repositories.content import ContentRepo
from bot.repositories.game import AccuracyEntry, GameRepo, RoundAnswer, SessionTotal
from bot.repositories.maintenance import MaintenanceRepo
from bot.repositories.member import MemberRepo
from bot.repositories.scheduler import SchedulerRepo
from bot.repositories.score import LeaderboardEntry, ScoreRepo
from bot.repositories.streak import StreakRepo
from bot.repositories.usage import UsageRepo

__all__ = [
    "AccuracyEntry",
    "AdminActionRepo",
    "BaseRepo",
    "ChatRepo",
    "ContentRepo",
    "GameRepo",
    "LeaderboardEntry",
    "MaintenanceRepo",
    "MemberRepo",
    "RoundAnswer",
    "SchedulerRepo",
    "ScoreRepo",
    "SessionTotal",
    "StreakRepo",
    "UsageRepo",
]
