# LinguaFlame Club

Групповой Telegram-бот для изучения английского: слова и грамматика дня,
мини-игры, рейтинг и общий огонёк группы.

## Стек

Python 3.12, aiogram 3, PostgreSQL (SQLite для разработки), SQLAlchemy 2
async + Alembic, APScheduler, ИИ через OpenAI-совместимый API.

## Запуск

```bash
# 1. Зависимости
python -m venv .venv
.venv\Scripts\pip install -e ".[dev]"   # Linux/macOS: .venv/bin/pip

# 2. Настройки: скопируйте .env.example в .env и заполните BOT_TOKEN, AI_API_KEY
copy .env.example .env

# 3. Схема базы
.venv\Scripts\python -m alembic upgrade head

# 4. Бот
.venv\Scripts\python -m bot.main
```

## Команды

`/start`, `/setup`, `/settings`, `/quiz [n]`, `/games`, `/scramble`, `/gap`,
`/race`, `/truefalse`, `/word`, `/grammar`, `/top`, `/me`, `/streak`, `/stop`,
`/privacy`.

Автоматически: пост дня со словами и грамматикой, вечернее напоминание об
огоньке, утреннее сообщение о потерянном огоньке, вехи огонька (3, 7, 14, 30,
100 дней) и итоги недели по понедельникам.

## Тесты

```bash
.venv\Scripts\python -m pytest
.venv\Scripts\python -m ruff check .
```

## Ручная проверка планировщика

Планировщик раз в минуту смотрит на часы и решает, что кому отправить: пост
дня, напоминание об огоньке, итоги недели. Чтобы не ждать нужного времени
суток, «сейчас» планировщика можно сдвинуть переменной
`SCHEDULER_TIME_OFFSET` — она не меняет часовой пояс группы и не трогает
настройки чатов, только точку зрения планировщика на текущее время.

```dotenv
# Планировщик считает, что сейчас на 3 часа позже
SCHEDULER_TIME_OFFSET=3h
# Уехать на сутки и 2 часа вперёд
SCHEDULER_TIME_OFFSET=1d2h
# Вернуться на 30 минут назад
SCHEDULER_TIME_OFFSET=-30m
# Число без единицы — минуты: это 90 минут вперёд
SCHEDULER_TIME_OFFSET=90
```

Поддерживаются дни (`d`), часы (`h`) и минуты (`m`), знак `-` сдвигает время
назад, `0` — обычный режим. Некорректное значение останавливает запуск с
понятной ошибкой.

Порядок проверки:

1. Поставьте `SCHEDULER_TICK_SECONDS=10`, чтобы не ждать минуту.
2. Установите время поста в группе (`/setup`) на ближайшее будущее или
   сдвиньте часы планировщика так, чтобы это время уже наступило.
3. Смотрите лог: `Чат <id>: отправляю пост дня (words)`. Повторный `tick`
   дубль не создаёт: в журнале уже есть запись за этот день.
4. Чтобы повторить проверку, удалите записи журнала и поста за нужный день
   (дата в журнале — локальная дата группы):
   ```sql
   DELETE FROM scheduled_messages WHERE chat_id = <id> AND date = (SELECT MAX(date) FROM scheduled_messages WHERE chat_id = <id>);
   DELETE FROM daily_posts WHERE chat_id = <id> AND date = (SELECT MAX(date) FROM daily_posts WHERE chat_id = <id>);
   ```
5. Верните `SCHEDULER_TIME_OFFSET=0` и обычный `SCHEDULER_TICK_SECONDS`.

Сдвиг действует только на автосообщения: игры, команды и `/word` работают по
настоящему времени. Сдвиг через полночь переносит и дату в журнале: пост за
«завтра» может уйти сегодня, а на следующий день его не будет. Это ожидаемо —
после проверки сразу верните `SCHEDULER_TIME_OFFSET=0`.

## Переменные окружения

Все настройки — в `.env.example` с комментариями. Планировщик:

| переменная | по умолчанию | что делает |
|---|---|---|
| `SCHEDULER_TICK_SECONDS` | 60 | как часто проверять чаты |
| `SCHEDULER_TIME_OFFSET` | 0 | сдвиг «сейчас» для ручной проверки |
| `SCHEDULER_REMINDER_TIME` | 20:00 | вечернее напоминание об огоньке |
| `SCHEDULER_STREAK_LOST_TIME` | 09:00 | утреннее сообщение о потере огонька |
| `SCHEDULER_JITTER_MAX_SECONDS` | 120 | разброс автосообщений между группами |
| `SCHEDULER_MESSAGES_PER_DAY` | 2 | предел автосообщений на чат в сутки |
| `SCHEDULER_DORMANT_DAYS` | 7 | через сколько дней без очков замолчать |
| `SCHEDULER_CONTENT_ATTEMPTS` | 3 | попытки собрать контент поста дня |
| `SCHEDULER_RETRY_DELAY_SECONDS` | 600 | пауза между попытками |
