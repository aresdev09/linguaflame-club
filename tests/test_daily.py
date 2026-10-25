"""Тесты слов и грамматики дня: общий кеш, сохранённый пост и «Got it».

Главные правила этапа: контент дня общий для групп одного уровня и языка,
повторный `/word` в тот же день не обращается к ИИ, а кнопка «Got it»
даёт очки один раз в день на участника.
"""

from datetime import UTC, date, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

from aiogram.types import CallbackQuery, Chat, Message, User
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from bot.config import Settings
from bot.locales import t
from bot.models import Chat as ChatRow
from bot.models import ContentCache, ScoreEvent
from bot.repositories import ChatRepo, ContentRepo, ScoreRepo, StreakRepo, UsageRepo
from bot.routers.daily import daily_button, grammar_command, word_command
from bot.services.ai_errors import AIRequestError
from bot.services.content import REASON_AI, REASON_QUOTA, ContentService
from bot.services.daily import (
    KIND_WORDS,
    MESSAGE_LIMIT,
    DailyService,
    TapResult,
    format_grammar_post,
    format_words_post,
    tap_key,
)
from bot.services.daily_callbacks import (
    build_got_callback,
    build_show_callback,
    parse_daily_callback,
)
from bot.services.fake_ai_client import FakeAIClient, default_grammar_lesson
from bot.services.scoring import ScoringService
from bot.services.streak import StreakService

CHAT_ID = -950
OTHER_CHAT_ID = -951
GROUP = Chat(id=CHAT_ID, type="group", title="Study group")
BOT_USER = User(id=42, is_bot=True, first_name="Club bot")
ANNA = User(id=55, is_bot=False, first_name="Anna")
BEKZOD = User(id=66, is_bot=False, first_name="Bekzod")


def _settings(**overrides: object) -> Settings:
    return Settings(bot_token="123:TEST", _env_file=None, **overrides)


def _today() -> date:
    return datetime.now(UTC).date()


async def _chat(
    session_factory: async_sessionmaker[AsyncSession],
    chat_id: int = CHAT_ID,
    *,
    level: str = "B1",
    language: str = "ru",
    words: int = 10,
) -> ChatRow:
    """Создаёт чат с настройками для теста."""
    async with session_factory() as session:
        async with session.begin():
            repo = ChatRepo(session)
            await repo.get_or_create(chat_id, title="G")
            await repo.update_settings(
                chat_id,
                level=level,
                translation_language=language,
                words_count=words,
            )
    async with session_factory() as session:
        chat = await ChatRepo(session).get(chat_id)
    assert chat is not None
    return chat


def _service(
    session_factory: async_sessionmaker[AsyncSession],
    client: FakeAIClient,
    *,
    settings: Settings | None = None,
) -> DailyService:
    settings = settings or _settings()
    return DailyService(
        session_factory=session_factory,
        settings=settings,
        content=ContentService(session_factory, client, settings),
        scoring=ScoringService(session_factory, settings),
        streak=StreakService(session_factory),
    )


def _message(user: User, text: str = "/word") -> Message:
    return Message(
        message_id=300,
        date=datetime.now(UTC),
        chat=GROUP,
        from_user=user,
        text=text,
    )


def _callback(user: User, data: str) -> CallbackQuery:
    return CallbackQuery(
        id="cb-daily",
        from_user=user,
        chat_instance="ci",
        data=data,
        message=Message(
            message_id=301,
            date=datetime.now(UTC),
            chat=GROUP,
            from_user=BOT_USER,
            text="📚 Words of the day (B1)",
        ),
    )


def _buttons(markup: object) -> list[str]:
    return [
        button.callback_data for row in markup.inline_keyboard for button in row
    ]


def _long_words(count: int) -> list[dict]:
    """Слова с примерами, чтобы пост не влез в одно сообщение."""
    return [
        {
            "word": f"word{index}",
            "transcription": f"/w{index}/",
            "translation": f"перевод {index}",
            "example": "Example sentence for the word. " * 8,
        }
        for index in range(count)
    ]


# --- Контент дня: общий кеш и сохранённый пост ---


async def test_two_chats_share_daily_words(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Группы одного уровня и языка получают один и тот же пост без второго ИИ."""
    first = await _chat(session_factory, CHAT_ID)
    second = await _chat(session_factory, OTHER_CHAT_ID)
    client = FakeAIClient()
    service = _service(session_factory, client)

    result_one = await service.words(first)
    result_two = await service.words(second)

    assert result_one.chunks == result_two.chunks
    assert len(client.calls_of("words")) == 1
    # У каждого чата своя запись поста дня со своим id для кнопки.
    assert result_one.post_id is not None
    assert result_two.post_id is not None
    assert result_one.post_id != result_two.post_id
    async with session_factory() as session:
        for chat_id in (CHAT_ID, OTHER_CHAT_ID):
            record = await ContentRepo(session).get_daily_post(
                chat_id, _today(), KIND_WORDS
            )
            assert record is not None and record.payload is not None


async def test_repeat_word_uses_saved_post_without_ai(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Повторный /word показывает сохранённый пост, даже если кеш пропал."""
    chat = await _chat(session_factory)
    client = FakeAIClient()
    service = _service(session_factory, client)

    first = await service.words(chat)
    async with session_factory() as session:
        async with session.begin():
            await session.execute(delete(ContentCache))
    second = await service.words(chat)

    assert second.chunks == first.chunks
    assert second.post_id == first.post_id
    assert len(client.calls_of("words")) == 1


async def test_word_command_repeats_from_saved_post(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """/word у двух участников: второй пост из сохранённой записи, без ИИ."""
    chat = await _chat(session_factory)
    client = FakeAIClient()
    service = _service(session_factory, client)
    sender = AsyncMock()
    sender.reply.return_value = SimpleNamespace(message_id=321)

    await word_command(_message(ANNA), chat, "en", sender, service)
    first_text = sender.reply.await_args.args[1]
    markup = sender.reply.await_args.kwargs["reply_markup"]
    assert "Words of the day (B1)" in first_text
    assert [button.text for row in markup.inline_keyboard for button in row] == [
        "Got it"
    ]

    await word_command(_message(BEKZOD), chat, "en", sender, service)

    assert sender.reply.await_args.args[1] == first_text
    assert len(client.calls_of("words")) == 1
    async with session_factory() as session:
        record = await ContentRepo(session).get_daily_post(CHAT_ID, _today(), KIND_WORDS)
    assert record is not None and record.message_id == 321
    assert record.posted_at is not None


async def test_words_quota_exhausted_returns_reason(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Без кеша и с исчерпанной квотой пост собрать не из чего."""
    await _chat(session_factory)
    client = FakeAIClient()
    service = _service(session_factory, client)
    sender = AsyncMock()
    async with session_factory() as session:
        async with session.begin():
            await ChatRepo(session).update_settings(CHAT_ID, ai_quota_per_day=1)
            await UsageRepo(session).increment(CHAT_ID, _today())
    async with session_factory() as session:
        chat = await ChatRepo(session).get(CHAT_ID)
    assert chat is not None

    result = await service.words(chat)
    await word_command(_message(ANNA), chat, "en", sender, service)

    assert result.chunks == []
    assert result.reason == REASON_QUOTA
    assert client.calls_of("words") == []
    assert sender.reply.await_args.args[1] == t("daily.no_content_quota", "en")


async def test_ai_error_returns_reason(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """ИИ недоступен: пост не показываем, причину сообщаем роутеру."""
    chat = await _chat(session_factory)
    client = FakeAIClient(error=AIRequestError("down"))
    service = _service(session_factory, client)

    result = await service.words(chat)

    assert result.chunks == []
    assert result.reason == REASON_AI


async def test_mark_sent_saves_message_id(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Запись поста обновляется после отправки: id сообщения с кнопкой."""
    chat = await _chat(session_factory)
    client = FakeAIClient()
    service = _service(session_factory, client)

    result = await service.words(chat)
    await service.mark_sent(result.post_id, 777)

    assert result.post_id is not None
    async with session_factory() as session:
        record = await ContentRepo(session).get_daily_post_by_id(result.post_id)
    assert record is not None and record.message_id == 777


# --- Форматирование постов ---


def test_words_post_has_lines_examples_and_footer() -> None:
    words = [
        {
            "word": "persuade",
            "transcription": "/pərˈsweɪd/",
            "translation": "убеждать",
            "example": "She persuaded him to stay.",
        }
    ]

    chunks = format_words_post("B1", words, "en", points=3)

    assert len(chunks) == 1
    text = chunks[0]
    assert "📚 Words of the day (B1)" in text
    assert "1. <b>persuade</b> /pərˈsweɪd/ — убеждать" in text
    assert "She persuaded him to stay." in text
    assert text.endswith('Tap "Got it" — 3 points to your score!')


def test_words_post_is_split_by_words() -> None:
    """20 слов с примерами не влезают в одно сообщение — режем по словам."""
    chunks = format_words_post("B1", _long_words(20), "en", points=3)

    assert len(chunks) > 1
    assert all(len(chunk) <= MESSAGE_LIMIT for chunk in chunks)
    assert "1. <b>word0</b>" in chunks[0]
    assert "20. <b>word19</b>" in chunks[-1]  # нумерация сквозная
    assert chunks[-1].endswith('Tap "Got it" — 3 points to your score!')


def test_content_is_escaped_for_html() -> None:
    """Разметку из контента ИИ не пропускаем в HTML-сообщение."""
    words = [
        {
            "word": "<b>bad</b>",
            "transcription": "x",
            "translation": "<script>",
            "example": "a < b & c",
        }
    ]

    text = format_words_post("B1", words, "en", points=3)[0]

    assert "<b>bad</b>" not in text
    assert "&lt;b&gt;bad&lt;/b&gt;" in text
    assert "a &lt; b &amp; c" in text


def test_grammar_post_and_answer() -> None:
    lesson = default_grammar_lesson().model_dump()

    text = format_grammar_post("B1", lesson, "en")

    assert text.startswith("📘 Grammar of the day: Present Simple (B1)")
    assert "Regular actions in the present." in text
    assert "• I read every day." in text
    assert "✏️ Exercise:" in text
    assert "B. reads" in text


def test_parse_daily_callback() -> None:
    assert parse_daily_callback(build_got_callback(7)) == ("got", 7)
    assert parse_daily_callback(build_show_callback(7)) == ("show", 7)
    assert parse_daily_callback("g:1:2:3") is None
    assert parse_daily_callback("daily:got") is None
    assert parse_daily_callback("daily:got:abc") is None
    assert parse_daily_callback("daily:oops:1") is None
    assert parse_daily_callback(None) is None


def test_button_data_carries_no_answer() -> None:
    """В callback_data кнопки только действие и id записи."""
    data = build_show_callback(15)

    assert data == "daily:show:15"
    assert "read" not in data


# --- Грамматика дня ---


async def test_grammar_command_shows_lesson_and_answer(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """/grammar публикует урок, «Show answer» дописывает ответ упражнения."""
    chat = await _chat(session_factory)
    client = FakeAIClient()
    service = _service(session_factory, client)
    sender = AsyncMock()
    sender.reply.return_value = SimpleNamespace(message_id=555)

    await grammar_command(_message(ANNA, "/grammar"), chat, "en", sender, service)

    text = sender.reply.await_args.args[1]
    assert "Grammar of the day: Present Simple (B1)" in text
    markup = sender.reply.await_args.kwargs["reply_markup"]
    data = _buttons(markup)[0]
    assert data.startswith("daily:show:")

    await daily_button(_callback(ANNA, data), chat, "en", sender, service)

    chat_id, message_id, edited = sender.edit.await_args.args
    assert (chat_id, message_id) == (CHAT_ID, 301)
    assert edited.startswith(text)
    assert "✅ Correct: B. reads" in edited
    assert "With he we use reads." in edited
    assert len(client.calls_of("grammar")) == 1


async def test_grammar_saved_post_is_reused(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Повторная /grammar тоже не тратит запрос к ИИ."""
    chat = await _chat(session_factory)
    client = FakeAIClient()
    service = _service(session_factory, client)

    first = await service.grammar(chat)
    second = await service.grammar(chat)

    assert second.chunks == first.chunks
    assert len(client.calls_of("grammar")) == 1


# --- Кнопка «Got it» ---


async def test_got_it_awards_once_per_day(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Очки за «Got it»: один раз в день на участника, каждому свои."""
    chat = await _chat(session_factory)
    client = FakeAIClient()
    service = _service(session_factory, client)
    result = await service.words(chat)
    assert result.post_id is not None

    first = await service.claim_points(chat, ANNA.id, result.post_id)
    second = await service.claim_points(chat, ANNA.id, result.post_id)
    other = await service.claim_points(chat, BEKZOD.id, result.post_id)

    assert first is TapResult.AWARDED
    assert second is TapResult.ALREADY
    assert other is TapResult.AWARDED
    async with session_factory() as session:
        anna = await ScoreRepo(session).total(CHAT_ID, ANNA.id)
        bekzod = await ScoreRepo(session).total(CHAT_ID, BEKZOD.id)
        source = await ScoreRepo(session).best_source(CHAT_ID, ANNA.id)
        events = await session.execute(
            select(ScoreEvent).where(ScoreEvent.user_id == ANNA.id)
        )
    assert anna == 3
    assert bekzod == 3
    assert source == "daily_bonus"
    assert len(list(events.scalars().all())) == 1


async def test_got_it_button_replies_with_points(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Кнопка начисляет очки и отвечает тостом; повтор — отказ без очков."""
    chat = await _chat(session_factory)
    client = FakeAIClient()
    service = _service(session_factory, client)
    result = await service.words(chat)
    assert result.post_id is not None
    sender = AsyncMock()
    data = build_got_callback(result.post_id)

    await daily_button(_callback(ANNA, data), chat, "en", sender, service)
    await daily_button(_callback(ANNA, data), chat, "en", sender, service)

    first_text, second_text = [call.args[1] for call in sender.answer_callback.await_args_list]
    assert first_text == "✅ +3"
    assert "already got today's points" in second_text
    assert sender.edit.await_args is None  # кнопка не правит пост
    async with session_factory() as session:
        total = await ScoreRepo(session).total(CHAT_ID, ANNA.id)
    assert total == 3


async def test_got_it_marks_group_activity(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """«Got it» засчитывает день активности для огонька группы."""
    chat = await _chat(session_factory)
    client = FakeAIClient()
    service = _service(session_factory, client)
    result = await service.words(chat)
    assert result.post_id is not None

    await service.claim_points(chat, ANNA.id, result.post_id)

    async with session_factory() as session:
        streak = await StreakRepo(session).get(CHAT_ID)
    assert streak is not None
    assert streak.current_streak == 1


async def test_got_it_rejects_foreign_and_stale_posts(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Кнопка из другого чата, другого вида или другого дня очков не даёт."""
    chat = await _chat(session_factory)
    other = await _chat(session_factory, OTHER_CHAT_ID)
    client = FakeAIClient()
    service = _service(session_factory, client)

    words = await service.words(other)
    grammar = await service.grammar(chat)
    assert words.post_id is not None and grammar.post_id is not None
    async with session_factory() as session:
        async with session.begin():
            yesterday = await ContentRepo(session).create_daily_post(
                CHAT_ID,
                _today() - timedelta(days=1),
                KIND_WORDS,
                payload={"words": []},
            )

    assert await service.claim_points(chat, ANNA.id, words.post_id) is TapResult.STALE
    assert await service.claim_points(chat, ANNA.id, grammar.post_id) is TapResult.STALE
    assert await service.claim_points(chat, ANNA.id, yesterday.id) is TapResult.STALE
    assert await service.claim_points(chat, ANNA.id, 0) is TapResult.STALE
    async with session_factory() as session:
        assert await ScoreRepo(session).total(CHAT_ID, ANNA.id) == 0


async def test_got_it_respects_daily_cap(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Дневной лимит режет награду, а исчерпанный — не даёт её вовсе."""
    chat = await _chat(session_factory)
    client = FakeAIClient()
    service = _service(session_factory, client, settings=_settings(score_daily_cap=2))
    result = await service.words(chat)
    assert result.post_id is not None
    async with session_factory() as session:
        async with session.begin():
            await ScoreRepo(session).add_event(
                CHAT_ID, BEKZOD.id, points=2, source="quiz"
            )

    anna = await service.claim_points(chat, ANNA.id, result.post_id)
    bekzod = await service.claim_points(chat, BEKZOD.id, result.post_id)

    assert anna is TapResult.AWARDED  # 3 очка урезаны до остатка лимита
    assert bekzod is TapResult.CAPPED  # лимит уже выбран
    async with session_factory() as session:
        assert await ScoreRepo(session).total(CHAT_ID, ANNA.id) == 2
        assert await ScoreRepo(session).total(CHAT_ID, BEKZOD.id) == 2


async def test_stale_button_answers_shortly(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Устаревшие кнопки не падают и отвечают коротко, очков не давая."""
    chat = await _chat(session_factory)
    client = FakeAIClient()
    service = _service(session_factory, client)
    sender = AsyncMock()

    await daily_button(
        _callback(ANNA, build_show_callback(999)), chat, "en", sender, service
    )
    await daily_button(
        _callback(ANNA, build_got_callback(999)), chat, "en", sender, service
    )

    sender.edit.assert_not_awaited()
    texts = [call.args[1] for call in sender.answer_callback.await_args_list]
    assert texts == ["This post is no longer active"] * 2
    async with session_factory() as session:
        assert await ScoreRepo(session).total(CHAT_ID, ANNA.id) == 0


def test_tap_result_texts_exist_in_both_languages() -> None:
    """У каждого итога нажатия есть текст и в en, и в ru."""
    for outcome in TapResult:
        assert t(tap_key(outcome), "en")
        assert t(tap_key(outcome), "ru")


async def test_unknown_daily_callback_is_ignored(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Чужие данные в кнопке: молча убираем «часики»."""
    chat = await _chat(session_factory)
    service = _service(session_factory, FakeAIClient())
    sender = AsyncMock()

    await daily_button(_callback(ANNA, "daily:nope:1"), chat, "en", sender, service)

    sender.answer_callback.assert_awaited_once()
    assert sender.answer_callback.await_args.args[1:] == ()
