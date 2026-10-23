"""Локализация бота: тексты по ключам, языки en и ru.

Все строки, которые видит пользователь, берутся отсюда через `t(key, lang)`.
"""

from typing import Final

from bot.locales import en, ru

DEFAULT_LANGUAGE: Final[str] = "en"
SUPPORTED_LANGUAGES: Final[tuple[str, ...]] = ("en", "ru")

_TEXTS: Final[dict[str, dict[str, str]]] = {"en": en.TEXTS, "ru": ru.TEXTS}


def t(key: str, lang: str | None = None, **kwargs: object) -> str:
    """Текст по ключу для языка группы.

    Неизвестный язык и отсутствие ключа в нём — откат на английский.
    Ключа нет ни в одном языке — KeyError, его перехватит ErrorMiddleware.
    """
    texts = _TEXTS.get(lang or "", en.TEXTS)
    text = texts.get(key)
    if text is None:
        text = en.TEXTS.get(key)
    if text is None:
        raise KeyError(f"Нет текста для ключа {key!r}")
    return text.format(**kwargs) if kwargs else text
