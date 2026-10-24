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
    # --- Личная статистика (/me) ---
    "me.title": "📊 Моя статистика",
    "me.week_points": "Очки за неделю: {points}",
    "me.all_time_points": "Очки за всё время: {points}",
    "me.place": "Место: #{place}",
    "me.no_place": "Место: —",
    "me.answers": "Ответы: {answers} ({accuracy}% верных)",
    "me.best_game": "Лучшая игра: {game}",
    # --- Названия игр ---
    "game.quiz": "Квиз",
    "game.scramble": "Собери слово",
    "game.gap": "Пропущенное слово",
    "game.race": "Гонка переводов",
    "game.truefalse": "Правда или ложь",
    # --- Игры: запуск и отказы ---
    "game.already_running": "Игра уже идёт. Остановите её командой /stop (админы) или подождите.",
    "game.cooldown": "Предыдущая игра только что закончилась. Немного подождите.",
    "game.daily_limit": "Дневной лимит игр для этой группы исчерпан. До завтра!",
    "game.no_content": "Не удалось подготовить вопросы. Попробуйте через минуту.",
    "game.no_content_quota": "Дневной лимит запросов к ИИ исчерпан. Попробуйте позже.",
    # --- Игры: ответы на кнопках ---
    "game.correct": "✅ Верно! +{points}",
    "game.correct_capped": "✅ Верно! Дневной лимит очков достигнут.",
    "game.wrong": "❌ Неверно",
    "game.already_answered": "Вы уже отвечали в этом раунде",
    "game.too_late": "Время раунда истекло",
    "game.round_over": "Этот раунд уже закончился",
    "game.no_game": "Игра уже закончилась",
    # --- Игры: раунды и итоги ---
    "game.stopped": "⏹ Игра остановлена",
    "game.results_empty": "В этот раз без очков — попробуйте ещё!",
    "game.correct_players": "✅ Ответили верно: {names}",
    "game.nobody_correct": "В этом раунде никто не ответил верно.",
    "game.flame": "Огонёк группы: {days} 🔥",
    "quiz.title": "🧠 Квиз — раунд {current}/{total}",
    "quiz.footer": "⏱ {seconds} с · один ответ на игрока",
    "quiz.correct_line": "✅ Правильный ответ: {letter}. {option}",
    "quiz.explanation_line": "💡 {explanation}",
    "quiz.finished": "🏁 Квиз завершён!",
    # --- Названия языков ---
    "lang.en": "English",
    "lang.ru": "Русский",
    "lang.uz": "O'zbekcha",
    "lang.tr": "Türkçe",
    "lang.es": "Español",
    "lang.de": "Deutsch",
}
