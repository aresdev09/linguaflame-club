"""Слова и грамматика дня: пост, сохранённый контент и очки за «Got it».

Контент дня общий для всех групп одного уровня и языка: его отдаёт
`ContentService` (общий кеш → ИИ). Здесь текст поста собирается из
сохранённого контента, а результат публикации (и ручной команды тоже)
фиксируется в `daily_posts` — поэтому повторный `/word` в тот же день
не обращается к ИИ и не создаёт дубль поста.

Пост может не влезть в одно сообщение Telegram: слова дня режутся на
несколько сообщений по словам, кнопка дня ставится на последнее.
"""

import html
import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime
from enum import Enum

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from bot.config import Settings
from bot.locales import t
from bot.models import Chat, DailyPost, utcnow
from bot.repositories import ContentRepo
from bot.services.content import REASON_QUOTA, ContentService
from bot.services.games.callbacks import OPTION_LETTERS
from bot.services.scoring import DailyAward, ScoringService, resolve_timezone
from bot.services.streak import StreakService

logger = logging.getLogger(__name__)

# Виды дневного контента — совпадают с `daily_posts.kind`.
KIND_WORDS = "words"
KIND_GRAMMAR = "grammar"

# Источник начисления очков за кнопку «Got it».
SOURCE_DAILY_BONUS = "daily_bonus"

# Telegram разрешает 4096 символов в сообщении; оставляем запас на разметку.
MESSAGE_LIMIT = 3500


class TapResult(Enum):
    """Итог нажатия «Got it»."""

    AWARDED = "awarded"
    ALREADY = "already"  # очки за сегодня уже получены
    CAPPED = "capped"  # дневной лимит очков
    STALE = "stale"  # пост не сегодняшний, из другой группы или его нет


@dataclass(frozen=True)
class DailyPostResult:
    """Готовый пост дня: сообщения, запись в `daily_posts` и причина пустоты."""

    chunks: list[str]
    post_id: int | None = None
    reason: str | None = None


def no_content_key(reason: str | None) -> str:
    """Ключ локализации для пустого поста дня."""
    if reason == REASON_QUOTA:
        return "daily.no_content_quota"
    return "daily.no_content"


def tap_key(outcome: TapResult) -> str:
    """Ключ локализации для ответа на кнопку «Got it»."""
    if outcome is TapResult.STALE:
        return "daily.stale"
    return f"daily.got_{outcome.value}"


def format_words_post(level: str, words: list[dict], lang: str, *, points: int) -> list[str]:
    """Собирает пост слов: транскрипция, перевод и пример каждого слова.

    Длинный пост (20 слов с примерами) режется на несколько сообщений
    по словам, чтобы каждое влезало в лимит Telegram.
    """
    header = t("daily.words_title", lang, level=level)
    blocks: list[str] = []
    for index, item in enumerate(words, start=1):
        block = t(
            "daily.word_line",
            lang,
            index=index,
            word=_escape(item.get("word")),
            transcription=_escape(item.get("transcription")),
            translation=_escape(item.get("translation")),
        )
        example = _escape(item.get("example"))
        if example:
            block += "\n" + t("daily.word_example", lang, example=example)
        blocks.append(block)
    footer = t("daily.words_footer", lang, points=points)
    return _split_post(header, blocks, footer)


def format_grammar_post(level: str, lesson: dict, lang: str) -> str:
    """Урок грамматики дня: объяснение, примеры и упражнение."""
    lines = [
        t(
            "daily.grammar_title",
            lang,
            topic=_escape(lesson.get("topic")),
            level=level,
        ),
        "",
        _escape(lesson.get("explanation")),
    ]
    examples = [item for item in (lesson.get("examples") or []) if str(item).strip()]
    if examples:
        lines += ["", t("daily.grammar_examples", lang)]
        lines += [f"• {_escape(item)}" for item in examples]
    exercise = lesson.get("exercise") or {}
    if exercise:
        lines += [
            "",
            t("daily.grammar_exercise", lang),
            _escape(exercise.get("question")),
        ]
        lines += [
            f"{letter}. {_escape(option)}"
            for letter, option in zip(OPTION_LETTERS, exercise.get("options") or [], strict=False)
        ]
    return "\n".join(lines)


def format_grammar_answer(lesson: dict, lang: str) -> str:
    """Ответ упражнения с пояснением: дописывается к уроку по кнопке."""
    exercise = lesson.get("exercise") or {}
    options = list(exercise.get("options") or [])
    try:
        index = int(exercise.get("correct_index", -1))
    except (TypeError, ValueError):
        index = -1
    answer = ""
    if 0 <= index < len(options):
        answer = f"{OPTION_LETTERS[index]}. {_escape(options[index])}"
    lines = [t("daily.grammar_answer_correct", lang, answer=answer)]
    explanation = _escape(lesson.get("answer_explanation"))
    if explanation:
        lines.append(t("daily.grammar_answer_explanation", lang, explanation=explanation))
    return "\n".join(lines)


def _escape(value: object) -> str:
    """Текст из контента: без лишних пробелов и безопасный для HTML."""
    return html.escape(str(value or "").strip())


def _split_post(header: str, blocks: list[str], footer: str) -> list[str]:
    """Режет пост по блокам: шапка в первом сообщении, подсказка — в последнем."""
    chunks: list[str] = []
    current = header
    for block in blocks:
        addition = f"\n{block}"
        if len(current) + len(addition) > MESSAGE_LIMIT:
            chunks.append(current)
            current = block
        else:
            current += addition
    chunks.append(current)
    if len(chunks[-1]) + len(footer) + 2 <= MESSAGE_LIMIT:
        chunks[-1] = f"{chunks[-1]}\n\n{footer}"
    else:
        chunks.append(footer)
    return chunks


class DailyService:
    """Пост дня для группы: сохранённый пост, общий кеш или ИИ."""

    def __init__(
        self,
        *,
        session_factory: async_sessionmaker[AsyncSession],
        settings: Settings,
        content: ContentService,
        scoring: ScoringService,
        streak: StreakService,
        now: Callable[[], datetime] = utcnow,
    ) -> None:
        self._session_factory = session_factory
        self._settings = settings
        self._content = content
        self._scoring = scoring
        self._streak = streak
        self._now = now

    @property
    def bonus_points(self) -> int:
        """Сколько очков даёт кнопка «Got it»."""
        return self._settings.score_daily_bonus

    # --- Слова и грамматика дня ---

    async def words(self, chat: Chat) -> DailyPostResult:
        """Слова дня; повторный вызов берёт сохранённый пост без запроса к ИИ."""
        today = self._today(chat)
        saved = await self._saved(chat, today, KIND_WORDS)
        if saved is not None:
            return saved

        result = await self._content.get_words(chat)
        if not result.items:
            return DailyPostResult([], reason=result.reason)
        payload = {
            "level": chat.level,
            "language": chat.ui_language,
            "words": result.items,
        }
        post_id = await self._save(chat.id, today, KIND_WORDS, payload)
        chunks = format_words_post(
            chat.level, result.items, chat.ui_language, points=self.bonus_points
        )
        return DailyPostResult(chunks, post_id=post_id)

    async def grammar(self, chat: Chat) -> DailyPostResult:
        """Грамматика дня; повторный вызов — сохранённый пост без ИИ."""
        today = self._today(chat)
        saved = await self._saved(chat, today, KIND_GRAMMAR)
        if saved is not None:
            return saved

        result = await self._content.get_grammar(chat)
        if not result.items:
            return DailyPostResult([], reason=result.reason)
        lesson = result.items[0]
        payload = {
            "level": chat.level,
            "language": chat.ui_language,
            "lesson": lesson,
        }
        post_id = await self._save(chat.id, today, KIND_GRAMMAR, payload)
        return DailyPostResult(
            [format_grammar_post(chat.level, lesson, chat.ui_language)],
            post_id=post_id,
        )

    async def save_payload(self, chat: Chat, kind: str, payload: dict) -> int | None:
        """Сохраняет пост дня из готового контента и возвращает его id.

        Нужен запасному пути планировщика: когда ИИ не ответил, пост
        собирается из банка вопросов, но кнопка дня и защита от дублей
        должны работать так же, как у обычного поста.
        """
        return await self._save(chat.id, self._today(chat), kind, payload)

    async def mark_sent(self, post_id: int | None, message_id: int | None) -> None:
        """Отмечает пост отправленным: сохраняет id сообщения с кнопкой дня."""
        if post_id is None or message_id is None:
            return
        async with self._session_factory() as session:
            async with session.begin():
                await ContentRepo(session).mark_daily_post_sent(post_id, message_id)

    # --- Кнопки под постом ---

    async def claim_points(self, chat: Chat, user_id: int, post_id: int) -> TapResult:
        """Кнопка «Got it»: очки один раз в день на участника."""
        if await self._post(chat, post_id, KIND_WORDS) is None:
            return TapResult.STALE
        award = await self._scoring.award_once_per_day(
            chat.id, user_id, self.bonus_points, SOURCE_DAILY_BONUS
        )
        if award is DailyAward.AWARDED:
            await self._touch_streak(chat)
            return TapResult.AWARDED
        if award is DailyAward.ALREADY:
            return TapResult.ALREADY
        return TapResult.CAPPED

    async def grammar_answer(self, chat: Chat, post_id: int) -> str | None:
        """Урок с открытым ответом — для правки сообщения по кнопке."""
        record = await self._post(chat, post_id, KIND_GRAMMAR)
        if record is None or not record.payload:
            return None
        lesson = record.payload.get("lesson") or {}
        if not lesson:
            return None
        level, lang = self._render_context(chat, record.payload)
        return (
            f"{format_grammar_post(level, lesson, lang)}\n\n{format_grammar_answer(lesson, lang)}"
        )

    # --- Внутренняя механика ---

    async def _saved(self, chat: Chat, today: date, kind: str) -> DailyPostResult | None:
        """Сохранённый пост дня: показываем его без обращения к ИИ."""
        async with self._session_factory() as session:
            record = await ContentRepo(session).get_daily_post(chat.id, today, kind)
        if record is None or not record.payload:
            return None
        payload = record.payload
        level, lang = self._render_context(chat, payload)
        if kind == KIND_WORDS:
            words = list(payload.get("words") or [])
            if not words:
                return None
            return DailyPostResult(
                format_words_post(level, words, lang, points=self.bonus_points),
                post_id=record.id,
            )
        lesson = payload.get("lesson") or {}
        if not lesson:
            return None
        return DailyPostResult([format_grammar_post(level, lesson, lang)], post_id=record.id)

    async def _save(self, chat_id: int, today: date, kind: str, payload: dict) -> int | None:
        """Создаёт или дополняет запись поста дня, возвращает её id.

        Одна запись на чат, дату и вид (уникальный индекс): если её уже
        создал планировщик или другая команда, чужой контент не трогаем.
        """
        try:
            async with self._session_factory() as session:
                async with session.begin():
                    repo = ContentRepo(session)
                    record = await repo.get_daily_post(chat_id, today, kind)
                    if record is None:
                        record = await repo.create_daily_post(chat_id, today, kind, payload=payload)
                    elif record.payload is None:
                        record.payload = payload
                        await session.flush()
                    return record.id
        except IntegrityError:
            # Запись создали параллельно: читаем её и используем как есть.
            logger.info("Пост дня %s для чата %s уже создан", kind, chat_id)
            async with self._session_factory() as session:
                record = await ContentRepo(session).get_daily_post(chat_id, today, kind)
            return record.id if record is not None else None

    async def _post(self, chat: Chat, post_id: int, kind: str) -> DailyPost | None:
        """Пост дня по id: только сегодняшний, только из этого чата и вида."""
        if post_id <= 0:
            return None
        async with self._session_factory() as session:
            record = await ContentRepo(session).get_daily_post_by_id(post_id)
        if record is None or record.chat_id != chat.id or record.kind != kind:
            return None
        if record.date != self._today(chat):
            return None
        return record

    async def _touch_streak(self, chat: Chat) -> None:
        """День активности для огонька группы; ошибка не отменяет очки."""
        try:
            await self._streak.register_activity(chat.id, chat.timezone)
        except Exception:
            logger.exception("Не удалось обновить огонёк чата %s", chat.id)

    @staticmethod
    def _render_context(chat: Chat, payload: dict) -> tuple[str, str]:
        """Уровень и язык сохранённого поста: как при публикации, а не сейчас."""
        level = str(payload.get("level") or chat.level)
        lang = str(payload.get("language") or chat.ui_language)
        return level, lang

    def _today(self, chat: Chat) -> date:
        """Сегодняшняя дата по часовому поясу чата."""
        return self._now().astimezone(resolve_timezone(chat.timezone)).date()
