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
    "setup.time_prompt": ("Reply to this message with the post time as HH:MM, for example 09:00."),
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
    # --- Мини-игры ---
    "games.menu_title": "🎮 Games\n\nPick a game — it starts right away.",
    "scramble.title": "🔤 Word scramble — {current}/{total}",
    "scramble.letters": "Letters: <b>{letters}</b>",
    "scramble.hint": "💡 Hint: {hint}",
    "scramble.footer": "⏱ {seconds} s · reply to this message with the word",
    "scramble.correct_line": "✅ Word: <b>{word}</b>",
    "scramble.finished": "🏁 Word scramble finished!",
    "gap.title": "📝 Fill the gap — {current}/{total}",
    "gap.footer": "⏱ {seconds} s · one answer per player",
    "gap.correct_line": "✅ Correct: <b>{option}</b>",
    "gap.finished": "🏁 Fill the gap finished!",
    "race.title": "🏁 Translation race — {current}/{total}",
    "race.prompt": "Translate: <b>{word}</b>",
    "race.footer": "⏱ {seconds} s · faster answer = more points",
    "race.correct_line": "✅ {word} — <b>{option}</b>",
    "race.finished": "🏁 Translation race finished!",
    "truefalse.title": "🤔 True or false — {current}/{total}",
    "truefalse.footer": "⏱ {seconds} s · one answer per player",
    "truefalse.true": "True",
    "truefalse.false": "False",
    "truefalse.correct_line": "✅ Answer: <b>{answer}</b>",
    "truefalse.finished": "🏁 True or false finished!",
    # --- Слова и грамматика дня (/word, /grammar) ---
    "daily.words_title": "📚 Words of the day ({level})",
    "daily.word_line": "{index}. <b>{word}</b> {transcription} — {translation}",
    "daily.word_example": "   ▸ {example}",
    "daily.words_footer": 'Tap "Got it" — {points} points to your score!',
    "daily.got": "Got it",
    "daily.got_awarded": "✅ +{points}",
    "daily.got_already": "You already got today's points ✅",
    "daily.got_capped": "Daily points limit reached",
    "daily.stale": "This post is no longer active",
    "daily.grammar_title": "📘 Grammar of the day: {topic} ({level})",
    "daily.grammar_examples": "Examples:",
    "daily.grammar_exercise": "✏️ Exercise:",
    "daily.show_answer": "Show answer",
    "daily.grammar_answer_correct": "✅ Correct: {answer}",
    "daily.grammar_answer_explanation": "💡 {explanation}",
    "daily.no_content": "Couldn't prepare the content right now. Try again in a minute.",
    "daily.no_content_quota": "The daily AI limit is used up. Try again later or tomorrow.",
    # --- Огонёк группы (/streak) и автосообщения ---
    "streak.title": "🔥 Group flame",
    "streak.current": "Current streak: {days}",
    "streak.best": "Best streak: {days}",
    "streak.today_active": "Today: the group already earned points ✅",
    "streak.today_pending": (
        "Today: no points yet — play a game or tap \"Got it\" to keep the flame!"
    ),
    "streak.out": "The flame is out. Light it again with /quiz!",
    "streak.reminder": (
        "🔥 Your group flame is at risk! {days} in a row. One quick /quiz keeps it alive."
    ),
    "streak.lost": "The flame went out after {days}. Light it again with /quiz!",
    "streak.milestone": (
        "🔥 {days} in a row! The group flame is burning bright — keep it up!"
    ),
    # --- Итоги недели ---
    "recap.title": "📊 Week in review",
    "recap.winner": "🏆 Winner of the week: {name} — {points} pts",
    "recap.top": "Top 3:",
    "recap.accuracy": "🎯 Best accuracy: {name} — {accuracy}% ({answers} answers)",
    "recap.flame_record": "🔥 Flame record: {days}",
    # --- Формы слова «день» (выбирает plural_days) ---
    "plural.days_one": "{days} day",
    "plural.days_few": "{days} days",
    "plural.days_many": "{days} days",
    # --- Названия языков ---
    "lang.en": "English",
    "lang.ru": "Русский",
    "lang.uz": "O'zbekcha",
    "lang.tr": "Türkçe",
    "lang.es": "Español",
    "lang.de": "Deutsch",
}
