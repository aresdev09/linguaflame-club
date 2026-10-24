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
    "setup.timezone_manual": "✏️ Enter timezone manually",
    "setup.time_manual": "✏️ Enter time manually",
    "setup.timezone_prompt": (
        "Reply to this message with the timezone, for example Asia/Samarkand."
    ),
    "setup.time_prompt": (
        "Reply to this message with the post time as HH:MM, for example 09:00."
    ),
    "setup.bad_timezone": "I don't know this timezone. Try, for example, UTC or Asia/Samarkand.",
    "setup.bad_time": "The time should look like 09:00.",
    "setup.saved": "Saved ✅",
    # --- Рейтинг (/top) ---
    "top.title_week": "🏆 Leaderboard — this week",
    "top.title_all": "🏆 Leaderboard — all time",
    "top.period_week": "This week",
    "top.period_all": "All time",
    "top.empty": "No scores yet. Start with /quiz",
    "top.entry": "{place} {name} — {points} pts",
    "top.unknown_player": "Player {user_id}",
    # --- Личная статистика (/me) ---
    "me.title": "📊 Your stats",
    "me.week_points": "Points this week: {points}",
    "me.all_time_points": "Points all time: {points}",
    "me.place": "Place: #{place}",
    "me.no_place": "Place: —",
    "me.answers": "Answers: {answers} ({accuracy}% correct)",
    "me.best_game": "Best game: {game}",
    # --- Названия игр ---
    "game.quiz": "Quiz",
    "game.scramble": "Word scramble",
    "game.gap": "Fill the gap",
    "game.race": "Translation race",
    "game.truefalse": "True or false",
    # --- Игры: запуск и отказы ---
    "game.already_running": "A game is already running. Use /stop (admins) or wait.",
    "game.cooldown": "The previous game just ended. Please wait a moment.",
    "game.daily_limit": "Daily game limit reached for this group. See you tomorrow!",
    "game.no_content": "Couldn't prepare questions right now. Try again in a minute.",
    "game.no_content_quota": "The daily AI limit is used up. Try again later.",
    # --- Игры: ответы на кнопках ---
    "game.correct": "✅ Correct! +{points}",
    "game.correct_capped": "✅ Correct! Daily points limit reached.",
    "game.wrong": "❌ Wrong answer",
    "game.already_answered": "You already answered this round",
    "game.too_late": "Time is up for this round",
    "game.round_over": "This round is already over",
    "game.no_game": "The game is already over",
    # --- Игры: раунды и итоги ---
    "game.stopped": "⏹ Game stopped",
    "game.results_empty": "No points this time — try again!",
    "game.correct_players": "✅ Correct: {names}",
    "game.nobody_correct": "Nobody answered correctly this round.",
    "game.flame": "Group flame: {days} 🔥",
    "quiz.title": "🧠 Quiz — round {current}/{total}",
    "quiz.footer": "⏱ {seconds} s · one answer per player",
    "quiz.correct_line": "✅ Correct: {letter}. {option}",
    "quiz.explanation_line": "💡 {explanation}",
    "quiz.finished": "🏁 Quiz finished!",
    # --- Названия языков ---
    "lang.en": "English",
    "lang.ru": "Русский",
    "lang.uz": "O'zbekcha",
    "lang.tr": "Türkçe",
    "lang.es": "Español",
    "lang.de": "Deutsch",
}
