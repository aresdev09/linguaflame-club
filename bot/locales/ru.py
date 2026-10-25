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
    "setup.timezone_prompt": ("Ответьте на это сообщение часовым поясом, например Asia/Samarkand."),
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
    # --- Мини-игры ---
    "games.menu_title": "🎮 Мини-игры\n\nВыберите игру — она начнётся сразу.",
    "scramble.title": "🔤 Собери слово — {current}/{total}",
    "scramble.letters": "Буквы: <b>{letters}</b>",
    "scramble.hint": "💡 Подсказка: {hint}",
    "scramble.footer": "⏱ {seconds} с · ответьте на это сообщение словом",
    "scramble.correct_line": "✅ Слово: <b>{word}</b>",
    "scramble.finished": "🏁 «Собери слово» завершено!",
    "gap.title": "📝 Пропущенное слово — {current}/{total}",
    "gap.footer": "⏱ {seconds} с · один ответ на игрока",
    "gap.correct_line": "✅ Верно: <b>{option}</b>",
    "gap.finished": "🏁 «Пропущенное слово» завершено!",
    "race.title": "🏁 Гонка переводов — {current}/{total}",
    "race.prompt": "Переведите: <b>{word}</b>",
    "race.footer": "⏱ {seconds} с · чем быстрее, тем больше очков",
    "race.correct_line": "✅ {word} — <b>{option}</b>",
    "race.finished": "🏁 «Гонка переводов» завершена!",
    "truefalse.title": "🤔 Правда или ложь — {current}/{total}",
    "truefalse.footer": "⏱ {seconds} с · один ответ на игрока",
    "truefalse.true": "Правда",
    "truefalse.false": "Ложь",
    "truefalse.correct_line": "✅ Ответ: <b>{answer}</b>",
    "truefalse.finished": "🏁 «Правда или ложь» завершена!",
    # --- Слова и грамматика дня (/word, /grammar) ---
    "daily.words_title": "📚 Слова дня ({level})",
    "daily.word_line": "{index}. <b>{word}</b> {transcription} — {translation}",
    "daily.word_example": "   ▸ {example}",
    "daily.words_footer": "Нажмите «Got it» — +{points} к рейтингу!",
    "daily.got": "Got it",
    "daily.got_awarded": "✅ +{points}",
    "daily.got_already": "Очки за сегодня уже получены ✅",
    "daily.got_capped": "Дневной лимит очков достигнут",
    "daily.stale": "Этот пост уже неактуален",
    "daily.grammar_title": "📘 Грамматика дня: {topic} ({level})",
    "daily.grammar_examples": "Примеры:",
    "daily.grammar_exercise": "✏️ Упражнение:",
    "daily.show_answer": "Show answer",
    "daily.grammar_answer_correct": "✅ Верно: {answer}",
    "daily.grammar_answer_explanation": "💡 {explanation}",
    "daily.no_content": "Не удалось подготовить контент. Попробуйте через минуту.",
    "daily.no_content_quota": "Дневной лимит запросов к ИИ исчерпан. Попробуйте позже.",
    # --- Огонёк группы (/streak) и автосообщения ---
    "streak.title": "🔥 Огонёк группы",
    "streak.current": "Текущий огонёк: {days}",
    "streak.best": "Рекорд: {days}",
    "streak.today_active": "Сегодня: очки уже заработаны ✅",
    "streak.today_pending": (
        "Сегодня: очков пока нет — сыграйте в игру или нажмите «Got it»!"
    ),
    "streak.out": "Огонёк погас. Зажгите его снова командой /quiz!",
    "streak.reminder": (
        "🔥 Огонёк группы под угрозой! {days} подряд. Один быстрый /quiz — и он снова горит."
    ),
    "streak.lost": "Огонёк погас после {days}. Зажгите его снова командой /quiz!",
    "streak.milestone": "🔥 {days} подряд! Огонёк группы горит ярко — так держать!",
    # --- Итоги недели ---
    "recap.title": "📊 Итоги недели",
    "recap.winner": "🏆 Победитель недели: {name} — {points} очков",
    "recap.top": "Топ 3:",
    "recap.accuracy": "🎯 Лучшая точность: {name} — {accuracy}% ({answers} ответов)",
    "recap.flame_record": "🔥 Рекорд огонька: {days}",
    # --- Админ-команды: остановка игры и сброс рейтинга ---
    "admin.stop_no_game": "Сейчас нет активной игры.",
    "admin.reset_confirm": (
        "Сбросить рейтинг группы?\n\n"
        "Текущие очки сохранятся как отмена, их можно вернуть в течение "
        "24 часов. Нажмите «Подтвердить», чтобы продолжить."
    ),
    "admin.reset_yes": "Подтвердить",
    "admin.reset_no": "Отмена",
    "admin.reset_cancelled": "Сброс отменён. Ничего не изменилось.",
    "admin.reset_done": (
        "✅ Рейтинг сброшен: {users} участников, {points} очков отменено. "
        "Вернуть можно в течение 24 часов."
    ),
    "admin.reset_nothing": "Сбрасывать нечего — в группе пока нет очков.",
    "admin.reset_undo": "Вернуть рейтинг",
    "admin.reset_undone": "✅ Рейтинг восстановлен ({points} очков вернулось).",
    "admin.reset_none": "Нет недавнего сброса, который можно вернуть.",
    "admin.reset_too_old": "Этот сброс старше 24 часов, вернуть его больше нельзя.",
    # --- Приватность (/privacy) ---
    "privacy.title": "🔒 Ваши данные",
    "privacy.body": (
        "Я храню ваш id, имя пользователя, отображаемое имя и очки за игры "
        "в группе. Обычные сообщения я не читаю.\n\n"
        "Из рейтинга можно скрыться в любой момент и вернуться обратно."
    ),
    "privacy.hide": "Скрыться из рейтинга",
    "privacy.show": "Вернуться в рейтинг",
    "privacy.hidden": "Теперь вы скрыты из рейтинга. Играть по-прежнему можно.",
    "privacy.shown": "Вы снова видны в рейтинге.",
    # --- Формы слова «день» (выбирает plural_days) ---
    "plural.days_one": "{days} день",
    "plural.days_few": "{days} дня",
    "plural.days_many": "{days} дней",
    # --- Названия языков ---
    "lang.en": "English",
    "lang.ru": "Русский",
    "lang.uz": "O'zbekcha",
    "lang.tr": "Türkçe",
    "lang.es": "Español",
    "lang.de": "Deutsch",
}
