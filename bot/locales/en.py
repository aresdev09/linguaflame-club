"""Тексты интерфейса на английском (язык по умолчанию)."""

TEXTS: dict[str, str] = {
    # --- Общие ---
    "common.error": "Something went wrong. Please try again later.",
    "common.not_admin": "Only group admins can change settings.",
    # --- Приветствие в группе ---
    "welcome.added": (
        "Hi! I'm LinguaFlame Club: group games, a leaderboard and words "
        "of the day for English learners. An admin can set me up with /setup. "
        "Let's learn together! 🔥"
    ),
    # --- Меню настроек (/setup) ---
    "setup.header": "⚙️ Group settings",
    "setup.hint": "Tap a value to change it. An admin opens this menu with /setup.",
    "setup.level": "Level",
    "setup.words": "Words of the day",
    "setup.grammar": "Grammar of the day",
    "setup.words_count": "Words per post",
    "setup.ui_language": "Interface language",
    "setup.translation_language": "Translation language",
    "setup.timezone": "Timezone",
    "setup.daily_time": "Daily post time",
    "setup.quiet": "Quiet hours",
    "setup.on": "on",
    "setup.off": "off",
    "setup.manual": "✏️ Enter manually",
    "setup.timezone_prompt": (
        "Reply to this message with the timezone, for example Asia/Samarkand."
    ),
    "setup.time_prompt": (
        "Reply to this message with the post time as HH:MM, for example 09:00."
    ),
    "setup.bad_timezone": "I don't know this timezone. Try, for example, UTC or Asia/Samarkand.",
    "setup.bad_time": "The time should look like 09:00.",
    "setup.saved": "Saved ✅",
    # --- Названия языков ---
    "lang.en": "English",
    "lang.ru": "Русский",
    "lang.uz": "O'zbekcha",
    "lang.tr": "Türkçe",
    "lang.es": "Español",
    "lang.de": "Deutsch",
}
