"""Системный промпт для генерации контента.

Текст базового промпта — из скила `group-content-ai`. В ИИ уходят только
уровень и язык переводов: имена участников и названия групп не передаются.
"""

BASE_PROMPT = (
    "You are an English teacher creating content for a group learning bot.\n"
    "Level: {level} (CEFR). Translation language: {language}.\n"
    "Content must be safe for all ages. Reply with valid JSON only, no markdown fences."
)

# Требование безопасности: контент для учебной аудитории любого возраста.
SAFETY_RULE = (
    "Content must be safe for learners of any age: avoid violence, politics, "
    "adult topics, insults, drugs and any other unsafe subjects."
)


def build_system_prompt(level: str, language: str) -> str:
    """Системный промпт под уровень и язык переводов группы."""
    return BASE_PROMPT.format(level=level, language=language) + "\n" + SAFETY_RULE
