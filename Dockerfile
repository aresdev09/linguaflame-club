# Образ бота: Python 3.12, зависимости из pyproject.toml, запуск от непривилегированного пользователя.
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Зависимости отдельным слоем: меняются реже кода.
COPY pyproject.toml README.md ./
COPY bot ./bot
RUN pip install --no-cache-dir .

# Служебные файлы проекта: миграции, конфиг Alembic, банк вопросов, скрипты.
COPY alembic.ini ./
COPY migrations ./migrations
COPY scripts ./scripts
COPY data ./data
COPY docker/entrypoint.sh /usr/local/bin/entrypoint.sh

RUN chmod +x /usr/local/bin/entrypoint.sh \
    && useradd --create-home --uid 1000 bot \
    && chown -R bot:bot /app

USER bot

ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
CMD ["python", "-m", "bot.main"]
