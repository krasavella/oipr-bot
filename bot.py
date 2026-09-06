"""Телеграм-бот, который делает эдвайсы (мемы шрифтом Impact)."""

from __future__ import annotations

import asyncio
import logging
import os
import random
import re
from pathlib import Path

from telegram import BotCommand, BotCommandScopeChat, Message, Update
from telegram.constants import ChatAction
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

import banter
import effects
import people
from meme import TextTooLong, make_advice

logging.basicConfig(
    format="%(asctime)s %(levelname)s %(name)s — %(message)s", level=logging.INFO
)
logging.getLogger("httpx").setLevel(logging.WARNING)
log = logging.getLogger("advicebot")

def load_env() -> None:
    """Подтягивает .env рядом с ботом; настоящее окружение всегда главнее."""
    env_file = Path(__file__).parent / ".env"
    if not env_file.exists():
        return
    for line in env_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip("'\""))


def env_ids(name: str) -> tuple[int, ...]:
    """Список telegram id из настроек: «123, 456». Мусор молча пропускаем."""
    raw = os.getenv(name, "").replace(",", " ").split()
    return tuple(int(part) for part in raw if part.lstrip("+-").isdigit())


def env_id(name: str) -> int:
    """Один id из настроек; 0 — значит не задан."""
    ids = env_ids(name)
    return ids[0] if ids else 0


def env_names(name: str) -> tuple[str, ...]:
    """Имена-триггеры из настроек, в нижнем регистре."""
    return tuple(part.strip().lower() for part in os.getenv(name, "").split(",")
                 if part.strip())


load_env()


COMMAND_RE = re.compile(
    r"^/(?:эдвайс|адвайс|advice|мем|meme)(?:@[\w_]+)?(?:\s+([\s\S]*))?$",
    re.IGNORECASE,
)
# разделитель верх/низ: "/" или "|", экранируется обратным слэшем
SPLIT_RE = re.compile(r"(?<!\\)[/|]")

# обращение по имени вместо слэша: «оыъпр эдвайс верх/низ», «оыъпрбот, жмых 9»
WAKE_RE = re.compile(r"^оыъпр(?:бот)?(?:[\s,.:;!?—–-]+|$)", re.IGNORECASE)
# позвали по имени и попросили помощи — слэша в таком обращении нет
HELP_RE = re.compile(
    r"^(?:help|start|хелп|хэлп|помощь|помоги|команды|справка|что\s*умеешь)\b",
    re.IGNORECASE,
)

# keklol стоит раньше kek, обоссать раньше ссать: иначе альтернатива съест
# только начало слова
EFFECT_RE = re.compile(
    r"^/(cas|жмых|jpeg|jpg|шакал|keklol|kek|обоссать|обассать|ссать|piss)"
    r"(?:@[\w_]+)?(?:\s+([\s\S]*))?$",
    re.IGNORECASE,
)
EFFECT_ALIASES = {
    "жмых": "cas",
    "jpg": "jpeg",
    "шакал": "jpeg",
    "обассать": "обоссать",
    "ссать": "обоссать",
    "piss": "обоссать",
}
# все эффекты принимают (байты, число); зеркалам оно не нужно
EFFECTS = {
    "cas": effects.squish,
    "jpeg": effects.deepfry,
    "kek": lambda data, _strength: effects.mirror(data, "left"),
    "keklol": lambda data, _strength: effects.mirror(data, "right"),
    "обоссать": effects.piss,
}

HELP = (
    "Я делаю <b>эдвайсы</b> — картинка + текст шрифтом Impact.\n\n"
    "<b>Как пользоваться:</b>\n"
    "• Отправь картинку с подписью <code>/эдвайс верхний текст/нижний текст</code>\n"
    "• Или ответь этой командой на уже отправленную картинку\n"
    "• Или сначала кинь картинку, а следующим сообщением — команду\n\n"
    "<b>Ещё:</b>\n"
    "• Без слэша текст уходит вниз: <code>/эдвайс а пар не было</code>\n"
    "• Только верхний: <code>/эдвайс когда проснулся в 6 утра/</code>\n"
    "• Переносы строк ставишь сам — где нажал Enter, там и будет новая строка\n"
    "• Регистр сохраняется: напишешь строчными — будет строчными, "
    "КАПСОМ — будет капсом\n"
    "• Смайлики и символы работают: <code>/эдвайс мем 😂/★ 100% ₽ ✓</code> — "
    "они рисуются в том же стиле, что и текст\n"
    "• Вместо <code>/</code> можно писать <code>|</code>, а чтобы поставить "
    "слэш в самом тексте — экранируй его: <code>\\/</code>\n"
    "• Команды-синонимы: /advice, /мем, /meme\n"
    "• Слэш не обязателен — позови по имени: "
    "<code>оыъпр эдвайс верх/низ</code>, <code>оыъпр жмых 9</code>. "
    "Работает с любой командой\n\n"
    "<b>Порча картинок</b> — команда подписью к картинке или ответом на неё:\n"
    "• <code>/cas</code> — жмых: фон схлопывается, объекты слипаются\n"
    "• <code>/jpeg</code> — шакал: пересжимает картинку до артефактов\n"
    "• <code>/kek</code> — отражает левую половину направо\n"
    "• <code>/keklol</code> — отражает правую половину налево\n"
    "• <code>/обоссать</code> — поливает картинку жёлтой струёй\n"
    "• Сила эффекта — числом от 1 до 10: <code>/cas 9</code>, "
    "<code>/jpeg 10</code> (по умолчанию 5); у /обоссать число — "
    "номер струи от 1 до 7, без числа берётся случайная — и очень редко "
    "выпадает восьмая, которую номером не вызвать\n"
    "• Эффекты складываются: результат становится последней картинкой, "
    "так что следующая команда возьмёт уже его\n"
    "• Синонимы: /жмых, /шакал, /ссать\n\n"
    "<b>Болталка:</b>\n"
    "• Ответь на мою картинку любым текстом — огрызнусь по смыслу. "
    "Пошлёшь — пошлю обратно\n"
    "• Или просто позови по имени: <code>оыъпр ты живой?</code>\n"
    "• Ответишь стикером — прилетит стикер в ответ, под настроение твоего"
)

MAX_TEXT = 300

TOO_LONG = (
    "Слишком много слов — таким кеглем текст на картинке уже не прочитать. "
    "Сократи фразу или разбей её на строки (Enter в нужном месте)."
)

GREETING = (
    "Дизайн, я оыъпрбот! Я умею делать мемы, шакалить картинки "
    "разными методами и ссать!"
)

NEED_PHOTO = (
    "Нужна картинка: пришли её с этой подписью или ответь командой на картинку."
)
BROKEN_PHOTO = "Не смог обработать эту картинку, попробуй другую."

PHOTO_HINT = (
    "Картинку вижу. Теперь напиши "
    "/эдвайс верхний текст/нижний текст "
    "(или просто /эдвайс текст — он уйдёт вниз). "
    "Ещё умею портить: /cas, /jpeg, /kek, /keklol, /обоссать"
)

# сколько последних огрызаний и стикеров помним, чтобы не повторяться подряд
BANTER_MEMORY = 4
STICKER_MEMORY = 8

# У бота бывает личная неприязнь и личная симпатия: кому какие ответы —
# задаётся в .env, потому что у каждого хозяина списки свои. Правки админа
# (people.json) в любом случае главнее.
CRUEL_IDS = env_ids("CRUEL_IDS")      # злые ответы и иногда отказ
NEMESIS_IDS = env_ids("NEMESIS_IDS")  # этим не просто злые, а свои собственные
SWEET_IDS = env_ids("SWEET_IDS")      # а с этими бот совсем другой человек
CRUEL_NAMES = env_names("CRUEL_NAMES")  # опала по имени, а не по id
CRUEL_REFUSAL_CHANCE = 0.20

# хозяин бота: только ему видны и доступны команды ниже; 0 — хозяина нет
ADMIN_ID = env_id("ADMIN_ID")
ADMIN_RE = re.compile(
    r"^/(люблю|love|няша|sweet|ненавижу|hate|забыть|forget|списки|people)"
    r"(?:@[\w_]+)?(?:\s+([\s\S]*))?$",
    re.IGNORECASE,
)
ADMIN_ALIASES = {"love": "люблю", "sweet": "няша", "hate": "ненавижу",
                 "forget": "забыть", "people": "списки"}
ADMIN_HELP = (
    "<b>Хозяйские команды</b> (видишь их только ты):\n"
    "• <code>/люблю</code> — ответом на сообщение или <code>/люблю 12345 Вася</code>\n"
    "• <code>/няша</code> — так же; с ним бот становится няшным фембойчиком\n"
    "• <code>/ненавижу</code> — так же; ему злые ответы и отказы\n"
    "• <code>/забыть</code> — вернуть обычное отношение\n"
    "• <code>/списки</code> — кто где"
)
ADMIN_NEED_TARGET = (
    "Кого? Ответь этой командой на его сообщение или укажи id: "
    "<code>/ненавижу 12345 Вася</code>"
)


def parse_args(raw: str | None) -> tuple[str, str]:
    """Разбирает аргументы команды на верхний и нижний текст.

    Без разделителя весь текст уходит вниз — так эдвайсы пишут чаще всего.
    Переносы строк внутри части остаются как есть.
    """
    if not raw:
        return "", ""
    raw = raw.strip("\n").strip()
    parts = SPLIT_RE.split(raw, maxsplit=1)
    parts = [p.replace("\\/", "/").replace("\\|", "|").strip() for p in parts]
    if len(parts) == 1:
        return "", parts[0][:MAX_TEXT]
    return parts[0][:MAX_TEXT], parts[1][:MAX_TEXT]


def parse_strength(raw: str | None) -> int:
    """Сила эффекта из аргументов команды; лишние слова игнорируем."""
    token = raw.split()[0] if raw and raw.split() else ""
    if token.lstrip("+-").isdigit():
        return effects.clamp_strength(int(token))
    return effects.DEFAULT_STRENGTH


def parse_variant(raw: str | None) -> int | None:
    """Номер струи из аргументов /обоссать; без числа — None, то есть случайная."""
    token = raw.split()[0] if raw and raw.split() else ""
    if token.isdigit() and int(token) > 0:
        return int(token)
    return None


def strip_wake(text: str) -> tuple[str, bool]:
    """Отрезает обращение по имени: «оыъпр эдвайс …» → («эдвайс …», True).

    Так любую команду можно писать без слэша — телефонная клавиатура его
    вечно прячет, а имя бота набирается само.
    """
    match = WAKE_RE.match(text)
    if match is None:
        return text, False
    return text[match.end():].lstrip(), True


def photo_file_id(message: Message | None) -> str | None:
    """Достаёт file_id самой крупной картинки из сообщения."""
    if message is None:
        return None
    if message.photo:
        return message.photo[-1].file_id
    doc = message.document
    if doc and (doc.mime_type or "").startswith("image/"):
        return doc.file_id
    sticker = message.sticker
    if sticker and not sticker.is_animated and not sticker.is_video:
        return sticker.file_id
    return None


def find_photo(message: Message, context: ContextTypes.DEFAULT_TYPE) -> str | None:
    """Картинка команды: своя, из ответа или последняя в этом чате."""
    return (
        photo_file_id(message)
        or photo_file_id(message.reply_to_message)
        or context.chat_data.get("last_photo")
    )


def full_name(user: object) -> str:
    """Имя, фамилия и юзернейм одной строкой — для поиска и для списков."""
    return " ".join(
        part for part in (getattr(user, "first_name", None),
                          getattr(user, "last_name", None),
                          getattr(user, "username", None)) if part
    )


def mood_of(user: object) -> str | None:
    """Как бот относится к человеку: зло, ласково или никак.

    Записанное админом главнее прошитого — так его и можно «регулировать»:
    прощённый становится обычным, даже если он в списке в коде.
    """
    if user is None:
        return None
    user_id = getattr(user, "id", 0)
    stored = people.mood(user_id)
    if stored == people.NEUTRAL:
        return None
    if stored:
        mood = stored
    elif user_id in CRUEL_IDS or any(n in full_name(user).lower()
                                     for n in CRUEL_NAMES):
        mood = people.CRUEL
    elif user_id in SWEET_IDS:
        mood = people.SWEET
    else:
        return None
    # у отдельных людей набор фраз свой, самый злой
    return (banter.NEMESIS if mood == people.CRUEL and user_id in NEMESIS_IDS
            else mood)


def replied_to_our_photo(message: Message, bot_id: int) -> bool:
    """Сообщение — ответ на картинку, которую прислал сам бот."""
    source = message.reply_to_message
    return bool(
        source
        and source.photo
        and source.from_user
        and source.from_user.id == bot_id
    )


def pick_banter(context: ContextTypes.DEFAULT_TYPE, text: str,
                mood: str | None = None) -> str:
    """Огрызание по смыслу реплики, с оглядкой на последние ответы в чате."""
    said = context.chat_data.get("banter", [])
    answer = banter.reply(text, said, mood)
    context.chat_data["banter"] = [*said, answer][-BANTER_MEMORY:]
    return answer


async def reply_sticker(message: Message,
                        context: ContextTypes.DEFAULT_TYPE) -> None:
    """Стикер в ответ на стикер: настроение читается по эмодзи присланного."""
    mood = mood_of(message.from_user)
    emoji = message.sticker.emoji if message.sticker else None
    seen = context.chat_data.get("stickers", [])
    file_id = banter.pick_sticker(context.bot_data.get("stickers", {}),
                                  emoji, seen, mood)
    if file_id is None:          # паки не загрузились — отвечаем хотя бы словами
        await message.reply_text(pick_banter(context, emoji or "", mood))
        return
    context.chat_data["stickers"] = [*seen, file_id][-STICKER_MEMORY:]
    await message.reply_sticker(file_id)


async def say_working(message: Message, context: ContextTypes.DEFAULT_TYPE,
                      mood: str | None) -> None:
    """Реплика перед картинкой: у кого какое отношение, тот такую и слышит."""
    said = context.chat_data.get("working", [])
    line = banter.working(mood, said)
    if line is None:
        return
    context.chat_data["working"] = [*said, line][-BANTER_MEMORY:]
    await message.reply_text(line)


async def refuses(message: Message) -> bool:
    """Иногда команда просто не выполняется. Иногда и не у всех."""
    mood = mood_of(message.from_user)
    if mood in (people.CRUEL, banter.NEMESIS) \
            and random.random() < CRUEL_REFUSAL_CHANCE:
        await message.reply_text(banter.refusal(mood))
        return True
    return False


def admin_target(message: Message, arg: str | None) -> tuple[int, str] | None:
    """Про кого команда: про автора сообщения в ответе или про id в аргументах."""
    words = (arg or "").split()
    if words and words[0].lstrip("+-").isdigit():
        return int(words[0]), " ".join(words[1:])
    source = message.reply_to_message
    if source is not None and source.from_user is not None:
        return source.from_user.id, full_name(source.from_user)
    return None


def admin_report() -> str:
    """Кто у бота в любимчиках, кто в опале, кого простили."""
    titles = {people.LOVED: "Любимчики", people.SWEET: "Няши",
              people.CRUEL: "В опале", people.NEUTRAL: "Прощённые"}
    lines = []
    for mood, rows in people.listing().items():
        who = ", ".join(f"{title or '?'} (<code>{uid}</code>)"
                        for uid, title in rows) or "никого"
        lines.append(f"<b>{titles[mood]}:</b> {who}")
    from_env = ", ".join([*(f"{i} (в опале)" for i in CRUEL_IDS),
                          *(f"{i} (в опале, лично)" for i in NEMESIS_IDS),
                          *(f"{i} (няша)" for i in SWEET_IDS)]) or "никого"
    lines.append(f"<b>Из .env:</b> {from_env}")
    return "\n".join(lines) + "\n\n" + ADMIN_HELP


async def run_admin(message: Message, name: str, arg: str | None) -> None:
    """Хозяйские команды. Сюда попадает только сам хозяин."""
    if name == "списки":
        await message.reply_html(admin_report())
        return

    target = admin_target(message, arg)
    if target is None:
        await message.reply_html(ADMIN_NEED_TARGET)
        return

    user_id, title = target
    mood, done = {
        "люблю": (people.LOVED, "теперь любимчик"),
        "няша": (people.SWEET, "теперь няша, бот с ним растает"),
        "ненавижу": (people.CRUEL, "теперь в опале"),
        "забыть": (people.NEUTRAL, "снова обычный человек"),
    }[name]
    people.remember(user_id, mood, title)
    log.info("админ: %s → %s", user_id, mood)
    await message.reply_html(f"{title or user_id} (<code>{user_id}</code>) — {done}")


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.effective_message.reply_html(HELP)


async def apply_effect(message: Message, context: ContextTypes.DEFAULT_TYPE,
                       name: str, arg: str | None) -> None:
    if await refuses(message):
        return

    file_id = find_photo(message, context)
    if not file_id:
        await message.reply_text(NEED_PHOTO)
        return

    # у /обоссать число в аргументах — номер струи, у остальных сила эффекта
    parse = parse_variant if name == "обоссать" else parse_strength

    await say_working(message, context, mood_of(message.from_user))
    await message.chat.send_action(ChatAction.UPLOAD_PHOTO)
    try:
        tg_file = await context.bot.get_file(file_id)
        source = bytes(await tg_file.download_as_bytearray())
        # жмых считает швы заметно дольше остальных — уводим в поток,
        # чтобы бот не замирал на это время
        result = await asyncio.to_thread(EFFECTS[name], source, parse(arg))
    except Exception:
        log.exception("не удалось применить /%s", name)
        context.chat_data["last_photo"] = file_id
        await message.reply_text(BROKEN_PHOTO)
        return

    sent = await message.reply_photo(result)
    # эффекты накладываются друг на друга: последней картинкой становится результат
    context.chat_data["last_photo"] = (
        sent.photo[-1].file_id if sent.photo else file_id
    )


async def on_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    if message is None:
        return

    raw = (message.text or message.caption or "").strip()
    # позвали по имени — дальше разбираем так же, как если бы стоял слэш
    body, called = strip_wake(raw)
    text = "/" + body.lstrip("/") if called else raw  # «оыъпр /жмых» тоже сойдёт

    # хозяйские команды: для всех остальных их просто не существует
    admin = ADMIN_RE.match(text)
    if admin is not None:
        user = message.from_user
        if ADMIN_ID and user is not None and user.id == ADMIN_ID:
            name = admin.group(1).lower()
            await run_admin(message, ADMIN_ALIASES.get(name, name), admin.group(2))
        return

    match = COMMAND_RE.match(text)
    effect = EFFECT_RE.match(text) if match is None else None

    # просто картинка без команды — запоминаем и подсказываем
    if match is None and effect is None:
        # «оыъпр» без ничего или с просьбой о помощи — это про /help
        if called and (not body or HELP_RE.match(body)):
            await message.reply_html(HELP)
            return
        user = message.from_user
        # ответ на нашу же картинку — значит, разговаривают с нами
        talking = bool(user and not user.is_bot
                       and replied_to_our_photo(message, context.bot.id))

        file_id = photo_file_id(message)
        if file_id:
            context.chat_data["last_photo"] = file_id

        # на стикер отвечаем стикером, а картинку при этом всё равно помним
        if message.sticker and talking:
            await reply_sticker(message, context)
            return
        if file_id:
            if message.chat.type == "private":
                await message.reply_text(PHOTO_HINT)
            return
        # позвали по имени или ответили на нашу картинку — говорят с нами
        if body and user and not user.is_bot and (called or talking):
            await message.reply_text(pick_banter(context, body, mood_of(user)))
        return

    if effect is not None:
        name = effect.group(1).lower()
        await apply_effect(message, context, EFFECT_ALIASES.get(name, name),
                           effect.group(2))
        return

    top, bottom = parse_args(match.group(1))
    if not top and not bottom:
        await message.reply_html(HELP)
        return

    if await refuses(message):
        return

    file_id = find_photo(message, context)
    if not file_id:
        await message.reply_text(NEED_PHOTO)
        return

    await say_working(message, context, mood_of(message.from_user))
    await message.chat.send_action(ChatAction.UPLOAD_PHOTO)
    try:
        tg_file = await context.bot.get_file(file_id)
        source = bytes(await tg_file.download_as_bytearray())
        result = make_advice(source, top, bottom)
    except TextTooLong:
        context.chat_data["last_photo"] = file_id
        await message.reply_text(TOO_LONG)
        return
    except Exception:
        log.exception("не удалось сделать эдвайс")
        await message.reply_text(BROKEN_PHOTO)
        return

    context.chat_data["last_photo"] = file_id
    await message.reply_photo(result)


async def on_new_members(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Здоровается: за себя — когда добавили бота, по имени — с остальными."""
    message = update.effective_message
    if message is None or not message.new_chat_members:
        return

    for member in message.new_chat_members:
        if member.id == context.bot.id:
            await message.reply_text(GREETING)
        else:
            await message.reply_text(f"Дизайн, {member.full_name}!")


async def on_error(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    log.error("ошибка при обработке апдейта", exc_info=context.error)


async def load_stickers(app: Application) -> None:
    """Складывает стикеры паков в память — из них потом выбираются ответы."""
    pairs: list[tuple[str | None, str]] = []
    for name in banter.STICKER_PACKS:
        try:
            pack = await app.bot.get_sticker_set(name)
        except Exception:
            log.warning("не открылся стикерпак %s", name)
            continue
        pairs += [(item.emoji, item.file_id) for item in pack.stickers]
    app.bot_data["stickers"] = banter.build_stickers(pairs)
    log.info("стикеров в запасе: %d", len(pairs))


ADMIN_MENU = [
    BotCommand("love", "в любимчики"),
    BotCommand("sweet", "в няши"),
    BotCommand("hate", "в опалу"),
    BotCommand("forget", "вернуть обычное отношение"),
    BotCommand("people", "кто где"),
]


async def post_init(app: Application) -> None:
    public = [
        BotCommand("advice", "сделать эдвайс: /advice верх/низ"),
        BotCommand("cas", "жмых: /cas [сила 1-10]"),
        BotCommand("jpeg", "шакал: /jpeg [сила 1-10]"),
        BotCommand("kek", "отразить левую половину направо"),
        BotCommand("keklol", "отразить правую половину налево"),
        BotCommand("piss", "обоссать картинку: /обоссать [струя 1-7]"),
        BotCommand("help", "как пользоваться"),
    ]
    await app.bot.set_my_commands(public)
    if ADMIN_ID:
        try:
            await app.bot.set_my_commands(public + ADMIN_MENU,
                                          scope=BotCommandScopeChat(ADMIN_ID))
        except Exception:
            # админ ещё не писал боту — меню появится при следующем запуске
            log.warning("не выставил хозяйское меню команд")
    await load_stickers(app)
    me = await app.bot.get_me()
    log.info("запущен как @%s", me.username)


def read_token() -> str:
    token = os.getenv("BOT_TOKEN", "").strip()
    if not token:
        raise SystemExit("Не найден BOT_TOKEN (переменная окружения или файл .env)")
    return token


def main() -> None:
    app = (
        Application.builder()
        .token(read_token())
        .post_init(post_init)
        .build()
    )
    app.add_handler(CommandHandler(["start", "help"], start))
    app.add_handler(
        MessageHandler(filters.StatusUpdate.NEW_CHAT_MEMBERS, on_new_members)
    )
    app.add_handler(
        MessageHandler(
            filters.TEXT
            | filters.CAPTION
            | filters.PHOTO
            | filters.Document.IMAGE
            | filters.Sticker.ALL,
            on_message,
        )
    )
    app.add_error_handler(on_error)
    log.info("поллинг запущен")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
