#!/bin/sh
# Подготовка к запуску: дождаться базы и привести схему к head, затем — бот.
set -e

# База может подниматься дольше бота: несколько попыток миграции.
attempt=1
max_attempts=30
until python -m alembic upgrade head; do
    if [ "$attempt" -ge "$max_attempts" ]; then
        echo "Миграции не применились за $max_attempts попыток" >&2
        exit 1
    fi
    echo "База ещё не готова, попытка $attempt/$max_attempts..." >&2
    attempt=$((attempt + 1))
    sleep 2
done

exec "$@"
