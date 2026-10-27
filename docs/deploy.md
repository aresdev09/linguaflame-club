# Деплой на сервер (этап 12, задачи 12.10–12.11)

Инструкция для сервера на Linux (Ubuntu/Debian). Бот работает через long
polling, поэтому публичный домен и вебхук не нужны. Действия ниже выполняются
**на сервере** — в рамках этапа они не запускались.

## Что понадобится

- Сервер с доступом по SSH (1 vCPU, 1 ГБ ОЗУ хватает).
- Токен бота у @BotFather и ключ ИИ.
- Аккаунт администратора в вашей учебной группе Telegram.

## Вариант A. Docker Compose (рекомендуется)

### 1. Подготовка сервера

```bash
sudo apt update && sudo apt upgrade -y
# Docker Engine и плагин compose
curl -fsSL https://get.docker.com | sudo sh
sudo usermod -aG docker "$USER"   # затем перелогиниться
docker --version && docker compose version
```

### 2. Код и настройки

```bash
git clone <адрес-репозитория> linguaflame-club
cd linguaflame-club
cp .env.example .env
nano .env
```

В `.env` заполните как минимум:

```dotenv
BOT_TOKEN=...
BOT_USERNAME=YourBotName
AI_SERVER_URL=https://integrate.api.nvidia.com/v1
AI_API_KEY=...
AI_MODEL=nvidia/nemotron-3-ultra-550b-a55b
AI_MODEL_FALLBACK=deepseek-ai/deepseek-v4.1-flash
LOG_JSON=true
POSTGRES_PASSWORD=<надёжный-пароль>
```

`DATABASE_URL` и `REDIS_URL` для контейнеров подставляет `docker-compose.yml` —
менять их в `.env` не нужно.

### 3. Запуск

```bash
docker compose up -d --build
docker compose ps                       # bot, db, redis — healthy/running
docker compose logs -f bot              # в логе строка о старте бота
```

Миграции применяются автоматически при старте бота (entrypoint ждёт готовности
PostgreSQL). Для запасного контента один раз загрузите банк вопросов:

```bash
docker compose exec bot python -m scripts.seed_question_bank --load
```

### 4. Обновление версии

```bash
git pull
docker compose build bot
docker compose up -d
docker compose logs -f bot
```

Миграции применятся при рестарте. Перед обновлением сделайте резервную копию
БД (см. ниже).

## Вариант B. Без Docker (systemd)

### 1. Код, зависимости, база

```bash
sudo apt update && sudo apt install -y python3.12 python3.12-venv postgresql redis-server
sudo -u postgres psql -c "CREATE USER linguaflame WITH PASSWORD '<пароль>';"
sudo -u postgres psql -c "CREATE DATABASE linguaflame_club OWNER linguaflame;"

git clone <адрес-репозитория> /opt/linguaflame-club
cd /opt/linguaflame-club
python3.12 -m venv .venv
.venv/bin/pip install .
cp .env.example .env && nano .env       # DATABASE_URL=postgresql+asyncpg://...
.venv/bin/python -m alembic upgrade head
.venv/bin/python -m scripts.seed_question_bank --load
```

### 2. Юнит systemd

`/etc/systemd/system/linguaflame-club.service`:

```ini
[Unit]
Description=LinguaFlame Club Telegram bot
After=network-online.target postgresql.service redis-server.service
Wants=network-online.target

[Service]
Type=simple
User=linguaflame
WorkingDirectory=/opt/linguaflame-club
ExecStart=/opt/linguaflame-club/.venv/bin/python -m bot.main
Restart=on-failure
RestartSec=5
EnvironmentFile=/opt/linguaflame-club/.env

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now linguaflame-club
sudo journalctl -u linguaflame-club -f
```

## Резервное копирование БД (12.11)

Резервная копия PostgreSQL — через `pg_dump`. Скрипт
`/opt/backup-linguaflame.sh`:

```bash
#!/bin/sh
set -e
DIR=/var/backups/linguaflame
mkdir -p "$DIR"
STAMP=$(date +%Y-%m-%d_%H-%M)
# Docker: docker compose exec -T db pg_dump -U linguaflame linguaflame_club
pg_dump -U linguaflame linguaflame_club | gzip > "$DIR/linguaflame_$STAMP.sql.gz"
# храним 14 дней
find "$DIR" -name 'linguaflame_*.sql.gz' -mtime +14 -delete
```

Для Docker используйте строку в комментарии (замените в скрипте).

```bash
chmod +x /opt/backup-linguaflame.sh
# ежедневно в 04:00
echo '0 4 * * * /opt/backup-linguaflame.sh' | sudo crontab -
```

Проверка восстановления (на тестовой базе):

```bash
createdb linguaflame_restore
gunzip -c /var/backups/linguaflame/linguaflame_<дата>.sql.gz | psql linguaflame_restore
```

Отдельно стоит копировать `.env` (в защищённое место) и, при необходимости,
том Redis.

## Мониторинг ошибок (12.11)

- **Логи.** При `LOG_JSON=true` бот пишет по одному JSON-событию на строку —
  удобно для `journald` и сборщиков. Уровни: `ERROR` — сбои, `WARNING` —
  восстановимые проблемы (чат недоступен, лимит Telegram), `INFO` — ход работы.
- **Просмотр.** Docker: `docker compose logs -f bot`. systemd:
  `journalctl -u linguaflame-club -f`.
- **Автоперезапуск.** `restart: unless-stopped` (Docker) или `Restart=on-failure`
  (systemd). Бот переживает падение и перезапуск без потери данных.
- **Проверка живости.** `docker compose ps` — сервисы должны быть `running`,
  `db` и `redis` — `healthy`. В логе ищите строки о старте планировщика и об
  обслуживании данных.
- **Оповещения (необязательно).** Поставьте `logwatch`/`vector`/`promtail` и
  настройте уведомление на строки уровня `ERROR` и на отсутствие heartbeat.

## Быстрый чек-лист после деплоя

- [ ] `docker compose ps` (или `systemctl status`) — всё запущено.
- [ ] В логе нет `ERROR`; токен принят Telegram.
- [ ] В тестовой группе отвечает `/help`, работает `/setup`.
- [ ] Рейтинг (`/top`) сохраняется после `docker compose restart bot`.
- [ ] Резервное копирование настроено и одна копия проверена восстановлением.
