"""Тексты интерфейса на русском."""

TEXTS: dict[str, str] = {
    # --- Общие ---
    "common.error": "Что-то пошло не так. Попробуйте позже.",
    "common.not_admin": "Настройки группы меняют только администраторы.",
    # --- Приветствие в группе ---
    "welcome.added": (
        "Привет! Я LinguaFlame Club: групповые игры, рейтинг и слова дня "
        "для изучающих английский. Администратор настроит меня командой /setup. "
        "Учимся вместе! 🔥"
    ),
    # --- Меню настроек (/setup) ---
    "setup.header": "⚙️ Настройки группы",
    "setup.hint": (
        "Нажмите на значение, чтобы изменить его. Меню открывает администратор командой /setup."
    ),
    "setup.level": "Уровень",
    "setup.words": "Слова дня",
    "setup.grammar": "Грамматика дня",
    "setup.words_count": "Слов в посте",
    "setup.ui_language": "Язык интерфейса",
    "setup.translation_language": "Язык переводов",
    "setup.timezone": "Часовой пояс",
    "setup.daily_time": "Время поста",
    "setup.quiet": "Тихие часы",
    "setup.on": "вкл",
    "setup.off": "выкл",
    "setup.timezone_manual": "✏️ Ввести часовой пояс вручную",
    "setup.time_manual": "✏️ Ввести время вручную",
    "setup.timezone_prompt": (
        "Ответьте на это сообщение часовым поясом, например Asia/Samarkand."
    ),
    "setup.time_prompt": (
        "Ответьте на это сообщение временем поста в формате ЧЧ:ММ, например 09:00."
    ),
    "setup.bad_timezone": (
        "Не знаю такой часовой пояс. Попробуйте, например, UTC или Asia/Samarkand."
    ),
    "setup.bad_time": "Время должно выглядеть так: 09:00.",
    "setup.saved": "Сохранено ✅",
    # --- Рейтинг (/top) ---
    "top.title_week": "🏆 Рейтинг — за неделю",
    "top.title_all": "🏆 Рейтинг — за всё время",
    "top.period_week": "За неделю",
    "top.period_all": "За всё время",
    "top.empty": "Очков пока нет. Начните с /quiz",
    "top.entry": "{place} {name} — {points} очк.",
    "top.unknown_player": "Игрок {user_id}",
    # --- Названия языков ---
    "lang.en": "English",
    "lang.ru": "Русский",
    "lang.uz": "O'zbekcha",
    "lang.tr": "Türkçe",
    "lang.es": "Español",
    "lang.de": "Deutsch",
}
