"""Тесты локализации: языки, откаты и полнота словарей."""

import pytest

from bot.locales import DEFAULT_LANGUAGE, SUPPORTED_LANGUAGES, en, plural_days, ru, t


def test_default_language_is_english() -> None:
    assert DEFAULT_LANGUAGE == "en"
    assert set(SUPPORTED_LANGUAGES) == {"en", "ru"}


def test_unknown_language_falls_back_to_english() -> None:
    assert t("setup.level", "xx") == en.TEXTS["setup.level"]
    assert t("setup.level", None) == en.TEXTS["setup.level"]
    assert t("setup.level", "") == en.TEXTS["setup.level"]


def test_russian_texts_are_returned_for_ru() -> None:
    assert t("setup.level", "ru") == ru.TEXTS["setup.level"]
    assert t("common.not_admin", "ru") == ru.TEXTS["common.not_admin"]


def test_missing_key_raises_key_error() -> None:
    with pytest.raises(KeyError):
        t("no.such.key", "en")
    with pytest.raises(KeyError):
        t("no.such.key", "ru")


def test_english_and_russian_dictionaries_have_same_keys() -> None:
    assert set(en.TEXTS) == set(ru.TEXTS)


def test_plural_days_uses_language_rules() -> None:
    """Форма слова «день»: в английском две, в русском три."""
    assert plural_days(1, "en") == "1 day"
    assert plural_days(2, "en") == "2 days"
    assert plural_days(21, "en") == "21 days"
    assert plural_days(1, "ru") == "1 день"
    assert plural_days(2, "ru") == "2 дня"
    assert plural_days(5, "ru") == "5 дней"
    assert plural_days(11, "ru") == "11 дней"
    assert plural_days(21, "ru") == "21 день"
