"""Системный промпт для генерации контента.

Текст базового промпта — из скила `group-content-ai`. В ИИ уходят только
уровень и язык переводов: имена участников и названия групп не передаются.
"""

BASE_PROMPT = (
    "You are an English teacher creating content for a group learning bot.\n"
    "Level: {level} (CEFR). Translation language: {language}.\n"
    "Content must be safe for all ages. Reply with valid JSON only, no markdown fences."
)


def build_system_prompt(level: str, language: str) -> str:
    """Системный промпт под уровень и язык переводов группы."""
    return BASE_PROMPT.format(level=level, language=language)
