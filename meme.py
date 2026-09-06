"""Рендер эдвайсов: картинка + текст шрифтом Impact с чёрной обводкой."""

from __future__ import annotations

import io
import struct
from dataclasses import dataclass
from pathlib import Path
from typing import NamedTuple

from PIL import Image, ImageDraw, ImageFont

FONTS_DIR = Path(__file__).parent / "fonts"
# Impact с кириллицей; если его нет — оригинальный, только латиница
FONT_PATH = FONTS_DIR / "ofont.ru_Impact.ttf"
if not FONT_PATH.exists():
    FONT_PATH = FONTS_DIR / "impact.ttf"

MAX_SIDE = 1600      # апскейл/даунскейл до вменяемого размера
MIN_SIDE = 500
SIDE_MARGIN_RATIO = 0.05    # отступ от боковых краёв
TOP_MARGIN_RATIO = 0.008    # узкая полоска воздуха над текстом и под ним,
BOTTOM_MARGIN_RATIO = 0.02  # чтобы обводка не срезалась краем кадра
BLOCK_RATIO = 0.42     # максимальная высота блока текста от высоты картинки
# Потолок кегля от высоты картинки. Замерен по эталонным мемам (photo_*.jpg):
# там строка занимает от 17.5% до 23.2% высоты кадра, 1/5 — середина разброса.
MAX_FONT_RATIO = 1 / 5
# Пол кегля считается от короткой стороны: у портретной картинки ширины на
# строку меньше, и мерка от высоты обрезала бы её вдвое раньше, чем квадратную.
MIN_FONT_RATIO = 1 / 45    # мельче текст на картинке уже не прочитать
MIN_FONT = 12              # абсолютный пол для совсем крошечных картинок
LINE_SPACING = 0.98        # межстрочный интервал от кегля строки
STROKE_RATIO = 0.05    # толщина обводки от кегля; в эталонах 4.8-6.8%
MIN_STROKE = 3
MAX_STROKE = 14        # потолок нужен только против совсем заплывших контрформ


class TextTooLong(ValueError):
    """Текст не влезает читаемым кеглем — рисовать такой мем незачем."""


class _Run(NamedTuple):
    """Кусок строки, который рисуется одним шрифтом."""

    text: str
    font: ImageFont.FreeTypeFont
    bold: int  # на сколько пикселей наращивать глиф, чтобы попасть в вес Impact


class _Placed(NamedTuple):
    """Строка с подобранным под неё кеглем."""

    text: str
    size: int
    height: int  # сколько места строка занимает по вертикали вместе с интервалом


@dataclass(frozen=True)
class _Fallback:
    """Шрифт для символов, которых нет в Impact."""

    path: Path
    variation: bytes | None  # начертание вариативного шрифта
    probe: str               # глиф, по которому подбирается кегль
    height: float            # его высота относительно капители Impact
    bold: float = 0.0        # искусственное поджирнение, доля кегля


# Порядок важен: символ рисуется первым шрифтом, где он есть.
# Все шрифты монохромные и жирные — в белом с чёрной обводкой они выглядят
# так же, как основной текст, а не как чужеродные цветные картинки.
# смайлики: 😀 🔥 👍 ❤ ⚡ ✅ — одним стилем со всем остальным текстом
EMOJI = _Fallback(FONTS_DIR / "NotoEmoji.ttf", b"Bold", "\U0001F600", 1.15, 0.012)
# стрелки и математика: → ⇒ ↔ ∑ √ ∞ ≠ ≤ ⊂ ∮
MATH = _Fallback(FONTS_DIR / "NotoSansMath-Regular.ttf", None, "H", 1.0, 0.055)

FALLBACKS: list[_Fallback] = [
    EMOJI,
    # ♪ ♫ ☹ ⌛ ☑ и прочая мелочь
    _Fallback(FONTS_DIR / "NotoSansSymbols.ttf", b"Black", "♫", 1.1, 0.01),
    # ★ ✓ ✗ ♡ ☘ — геометрия, галочки, дингбаты
    _Fallback(FONTS_DIR / "NotoSansSymbols2-Regular.ttf", None, "★", 0.85, 0.025),
    # ₽ ₴ ‰ ⟨⟩ — широкий текстовый шрифт
    _Fallback(FONTS_DIR / "NotoSans.ttf", b"Black", "H", 1.0),
    MATH,
]
# на машине могут стоять шрифты с ещё более широким покрытием (CJK и т.п.)
SYSTEM_FALLBACKS: list[_Fallback] = [
    _Fallback(Path("/usr/share/fonts/noto-cjk/NotoSansCJK-Black.ttc"), None, "H", 1.0),
    _Fallback(Path("/usr/share/fonts/noto/NotoSans-Bold.ttf"), None, "H", 1.0),
    _Fallback(Path("/usr/share/fonts/liberation/LiberationSans-Bold.ttf"), None, "H", 1.0),
    _Fallback(Path("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"), None, "H", 1.0),
]

# Стрелки и математику Impact унаследовал из DOS-набора — это волосяные линии,
# рядом с жирными буквами они выглядят инородно. Берём их из Noto Sans Math:
# там же лежат ⇒ ⇔ ∀ ∮, которых в Impact нет вовсе, и весь блок выходит единым.
_MATH_FIRST = set(range(0x2190, 0x2300)) | {0x2302, 0x2310, 0x2320, 0x2321}

# ☺ ☻ ☹ в Impact — крохотные значки из той же DOS-таблицы; кто их набирает,
# имеет в виду смайлик, а не типографский значок
_EMOJI_FIRST = {0x2639, 0x263A, 0x263B}

# символы, которые не выбирают шрифт сами, а прилипают к предыдущему:
# селекторы начертания, ZWJ, тона кожи, теги и комбинирующие диакритики.
# Так эмодзи-последовательности (👨‍👩‍👧, 👍🏽, 🇷🇺) остаются одним куском
# и harfbuzz склеивает их в единый глиф.
_CONTINUATION = (
    {0x200D, 0xFE0E, 0xFE0F, 0x20E3}
    | set(range(0x0300, 0x0370))
    | set(range(0x1F3FB, 0x1F400))
    | set(range(0xE0020, 0xE0080))
)

# неразрывные и прочие экзотические пробелы → обычный пробел,
# невидимки (мягкий перенос, нулевая ширина, BOM) → в мусор
_CLEANUP = (
    dict.fromkeys(map(ord, "\t\u00a0\u1680\u2000\u2001\u2002\u2003\u2004\u2005"
                           "\u2006\u2007\u2008\u2009\u200a\u202f\u205f\u3000"), " ")
    | dict.fromkeys(map(ord, "\u2028\u2029"), "\n")
    | dict.fromkeys(map(ord, "\u00ad\u200b\u200c\ufeff"), None)
)

_font_cache: dict[tuple, ImageFont.FreeTypeFont] = {}
_coverage_cache: dict[Path, frozenset[int]] = {}


# --------------------------------------------------------------------------
# какие символы шрифт умеет рисовать
# --------------------------------------------------------------------------

def _read_cmap(path: Path) -> frozenset[int]:
    """Разбирает таблицу cmap и возвращает кодпоинты с реальными глифами.

    FreeType через Pillow не умеет сказать, есть ли глиф, а рисовать «квадрат
    вместо буквы» нельзя — поэтому читаем таблицу сами.
    """
    data = path.read_bytes()
    if data[:4] == b"ttcf":  # коллекция — берём первый шрифт
        base = struct.unpack_from(">I", data, 12)[0]
    else:
        base = 0
    num_tables = struct.unpack_from(">H", data, base + 4)[0]
    cmap_off = None
    for i in range(num_tables):
        tag, _checksum, offset, _length = struct.unpack_from(
            ">4sIII", data, base + 12 + 16 * i
        )
        if tag == b"cmap":
            cmap_off = offset
            break
    if cmap_off is None:
        return frozenset()

    out: set[int] = set()
    for i in range(struct.unpack_from(">H", data, cmap_off + 2)[0]):
        pid, eid, sub_offset = struct.unpack_from(">HHI", data, cmap_off + 4 + 8 * i)
        if pid == 3 and eid not in (1, 10):
            continue  # не юникодная подтаблица (символьная/legacy)
        if pid not in (0, 3):
            continue
        sub = cmap_off + sub_offset
        fmt = struct.unpack_from(">H", data, sub)[0]
        if fmt == 4:
            _parse_format4(data, sub, out)
        elif fmt == 12:
            _parse_format12(data, sub, out)
        elif fmt == 6:
            first, count = struct.unpack_from(">HH", data, sub + 6)
            for k in range(count):
                if struct.unpack_from(">H", data, sub + 10 + 2 * k)[0]:
                    out.add(first + k)
        elif fmt == 0:
            out.update(c for c in range(256) if data[sub + 6 + c])
    return frozenset(out)


def _parse_format4(data: bytes, sub: int, out: set[int]) -> None:
    seg_x2 = struct.unpack_from(">H", data, sub + 6)[0]
    ends = sub + 14
    starts = ends + seg_x2 + 2
    deltas = starts + seg_x2
    ranges = deltas + seg_x2
    for s in range(seg_x2 // 2):
        end = struct.unpack_from(">H", data, ends + 2 * s)[0]
        start = struct.unpack_from(">H", data, starts + 2 * s)[0]
        delta = struct.unpack_from(">h", data, deltas + 2 * s)[0]
        range_offset = struct.unpack_from(">H", data, ranges + 2 * s)[0]
        for code in range(start, min(end, 0xFFFE) + 1):
            if range_offset == 0:
                gid = (code + delta) & 0xFFFF
            else:
                at = ranges + 2 * s + range_offset + 2 * (code - start)
                if at + 2 > len(data):
                    continue
                gid = struct.unpack_from(">H", data, at)[0]
                if gid:
                    gid = (gid + delta) & 0xFFFF
            if gid:
                out.add(code)


def _parse_format12(data: bytes, sub: int, out: set[int]) -> None:
    for g in range(struct.unpack_from(">I", data, sub + 12)[0]):
        start, end, gid = struct.unpack_from(">III", data, sub + 16 + 12 * g)
        if gid and start <= end and end - start < 0x20000:
            out.update(range(start, end + 1))


def _coverage(path: Path) -> frozenset[int]:
    if path not in _coverage_cache:
        try:
            _coverage_cache[path] = _read_cmap(path)
        except Exception:
            _coverage_cache[path] = frozenset()
    return _coverage_cache[path]


_available: list[_Fallback] | None = None


def _available_fallbacks() -> list[_Fallback]:
    """Шрифты, которые реально лежат на диске (проверяем один раз)."""
    global _available
    if _available is None:
        _available = [f for f in FALLBACKS + SYSTEM_FALLBACKS if f.path.exists()]
    return _available


# --------------------------------------------------------------------------
# шрифты
# --------------------------------------------------------------------------

def _load(path: Path, size: int, variation: bytes | None) -> ImageFont.FreeTypeFont:
    key = (path, size, variation)
    if key not in _font_cache:
        font = ImageFont.truetype(str(path), size)
        if variation:
            try:
                font.set_variation_by_name(variation)
            except Exception:
                pass  # не вариативный или нет такого начертания — рисуем как есть
        _font_cache[key] = font
    return _font_cache[key]


def _font(size: int) -> ImageFont.FreeTypeFont:
    return _load(FONT_PATH, size, None)


def _cap_height(size: int) -> float:
    box = _font(size).getbbox("H")
    return box[3] - box[1]


def _fallback_font(fb: _Fallback, size: int) -> ImageFont.FreeTypeFont:
    """Подгоняет кегль запасного шрифта под капитель Impact.

    У Noto другая метрика, и одинаковый кегль дал бы символы заметно мельче
    букв — поэтому меряем эталонный глиф и считаем масштаб.
    """
    key = ("fit", fb.path, size)
    if key not in _font_cache:
        probe_font = _load(fb.path, 100, fb.variation)
        box = probe_font.getbbox(fb.probe)
        probe_h = box[3] - box[1]
        target = _cap_height(size) * fb.height
        fitted = max(1, round(100 * target / probe_h)) if probe_h else size
        _font_cache[key] = _load(fb.path, fitted, fb.variation)
    return _font_cache[key]


def _pick(fb: _Fallback, size: int) -> tuple[ImageFont.FreeTypeFont, int]:
    font = _fallback_font(fb, size)
    return font, round(font.size * fb.bold)


def _font_for_char(char: str, size: int,
                   prefer: _Fallback | None = None) -> tuple[ImageFont.FreeTypeFont, int] | None:
    """Шрифт и поджирнение для символа; None — символа нет нигде."""
    code = ord(char)
    if prefer is not None and prefer in _available_fallbacks() and code in _coverage(prefer.path):
        return _pick(prefer, size)
    if code in _coverage(FONT_PATH):
        return _font(size), 0
    for fb in _available_fallbacks():
        if code in _coverage(fb.path):
            return _pick(fb, size)
    return None


def _runs(line: str, size: int) -> list[_Run]:
    """Режет строку на куски, каждый со своим шрифтом.

    Символы, которых нет ни в одном шрифте, выкидываем — «квадрат вместо
    буквы» на картинке хуже, чем его отсутствие.
    """
    runs: list[_Run] = []
    for i, char in enumerate(line):
        if runs and ord(char) in _CONTINUATION:
            runs[-1] = runs[-1]._replace(text=runs[-1].text + char)
            continue
        # VS16 после символа — пользователь набрал именно эмодзи (☺️, ♥️, 1️⃣),
        # тогда эмодзи-шрифт важнее Impact, даже если глиф в Impact есть
        if line[i + 1:i + 2] == "\ufe0f" or ord(char) in _EMOJI_FIRST:
            prefer = EMOJI
        elif ord(char) in _MATH_FIRST:
            prefer = MATH
        else:
            prefer = None
        picked = _font_for_char(char, size, prefer)
        if picked is None:
            continue
        font, bold = picked
        if runs and runs[-1].font is font:
            runs[-1] = runs[-1]._replace(text=runs[-1].text + char)
        else:
            runs.append(_Run(char, font, bold))
    return runs


def _ink_bounds(line: str, size: int) -> tuple[int, int]:
    """Насколько глифы строки выступают над базовой линией и под ней.

    Метрики шрифта дают запас сразу под все буквы алфавита, а нам нужен запас
    именно этой строки: под «МАЛЕНКОВ» пусто, под «пинджаб» висит хвост «д»,
    над «ГЕОРГИЙ» торчит бревис. По этим числам блок и прижимается к краю кадра
    — без пустой полосы там, где выносных элементов нет.
    """
    above = below = 0
    for run in _runs(line, size):
        box = run.font.getbbox(run.text)
        baseline = run.font.getmetrics()[0]
        above = max(above, baseline - box[1] + run.bold)
        below = max(below, box[3] - baseline + run.bold)
    return max(0, above), max(0, below)


def _stroke_width(font_size: int) -> int:
    """Жирная обводка в 5-6 пикселей; на мелком кегле — чуть тоньше."""
    return max(MIN_STROKE, min(MAX_STROKE, round(font_size * STROKE_RATIO)))


def _text_width(draw: ImageDraw.ImageDraw, line: str, size: int) -> float:
    return sum(draw.textlength(run.text, font=run.font) for run in _runs(line, size))


def _fit_size(draw: ImageDraw.ImageDraw, line: str, max_width: float,
              floor: int, cap: int) -> int:
    """Самый крупный кегль, при котором строка целиком влезает в max_width.

    Ширина строки растёт вместе с кеглем монотонно, поэтому кегль ищется
    делением пополам: строка получает ровно свой максимум, а не ближайшее
    значение, в которое попал шаг перебора. 0 — не влезает даже на нижней
    границе читаемости.
    """
    if not line or _text_width(draw, line, cap) <= max_width:
        return cap
    if _text_width(draw, line, floor) > max_width:
        return 0
    lo, hi = floor, cap
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if _text_width(draw, line, mid) <= max_width:
            lo = mid
        else:
            hi = mid - 1
    return lo


def _layout(text: str, draw: ImageDraw.ImageDraw, img_w: int,
            img_h: int) -> list[_Placed]:
    """Подбирает блоку кегль — по самой длинной его строке.

    Весь блок набирается одним кеглем: строки разного размера рядом читаются
    как ошибка вёрстки, а не как мем. Сам текст не переносится: строки ровно
    те, что задал пользователь, слова не рвутся и за края не выходят. Потолок
    кегля и отступы считаются от размеров картинки, так что на большой картинке
    текст выходит крупнее, а на маленькой мельче — при одинаковых пропорциях.
    Если даже на нижней границе читаемости строка не помещается — TextTooLong.
    """
    max_width = img_w - 2 * int(img_w * SIDE_MARGIN_RATIO)
    max_height = img_h * BLOCK_RATIO
    lines = [ln.strip() for ln in text.split("\n")]
    lines = [ln for ln in lines if ln] or [""]

    floor = max(MIN_FONT, int(min(img_w, img_h) * MIN_FONT_RATIO))
    ceiling = max(floor, int(img_h * MAX_FONT_RATIO))
    fits = [_fit_size(draw, ln, max_width, floor, ceiling) for ln in lines]
    if min(fits) == 0:
        raise TextTooLong
    size = min(fits)

    # блок целиком должен уместиться в отведённую по высоте полосу. Межстрочный
    # интервал зависит от кегля не линейно (обводка и щель между строками имеют
    # свой пол), поэтому кегль ужимается не одним делением, а до тех пор, пока
    # блок действительно не влезет.
    total = sum(_line_heights(lines, size))
    while total > max_height:
        size = min(size - 1, int(size * max_height / total))
        if size < floor:
            raise TextTooLong
        total = sum(_line_heights(lines, size))

    return [
        _Placed(line, size, height)
        for line, height in zip(lines, _line_heights(lines, size))
    ]


def _line_heights(lines: list[str], size: int) -> list[int]:
    """Расстояние от базовой линии каждой строки до базовой линии следующей.

    По умолчанию это плотный межстрочный интервал, как в эталонных мемах. Но
    капс не всегда плоский: под «ЦЩД» висят хвосты, над «ЙЁ» торчат бревисы,
    у строчных — выносные элементы. Там, где обводки соседних строк иначе
    слиплись бы, строки расходятся ровно настолько, чтобы между ними осталась
    щель — и ни пикселем больше.
    """
    nominal = round(size * LINE_SPACING)
    stroke = _stroke_width(size)
    heights = []
    for line, following in zip(lines, lines[1:]):
        clearance = (_ink_bounds(line, size)[1]
                     + _ink_bounds(following, size)[0] + 2 * stroke + 1)
        heights.append(max(nominal, clearance))
    heights.append(nominal)
    return heights


def _stamp(draw: ImageDraw.ImageDraw, x: float, baseline: float, run: _Run,
           color: str, width: int) -> None:
    draw.text(
        (x, baseline),
        run.text,
        font=run.font,
        fill=color,
        stroke_width=width,
        stroke_fill=color,
        anchor="ls",
    )


def _draw_line(draw: ImageDraw.ImageDraw, line: str, size: int,
               center_x: float, baseline: float, stroke: int) -> None:
    """Рисует строку по кускам, выравнивая их по общей базовой линии."""
    runs = _runs(line, size)
    if not runs:
        return
    widths = [draw.textlength(run.text, font=run.font) for run in runs]
    x = center_x - sum(widths) / 2
    for run, width in zip(runs, widths):
        if run.bold:
            # символы Noto тоньше Impact: наращиваем белую заливку обводкой
            # того же цвета, а чёрный контур рисуем отдельным слоем под ней
            _stamp(draw, x, baseline, run, "black", run.bold + stroke)
            _stamp(draw, x, baseline, run, "white", run.bold)
        else:
            draw.text(
                (x, baseline),
                run.text,
                font=run.font,
                fill="white",
                stroke_width=stroke,
                stroke_fill="black",
                anchor="ls",
            )
        x += width


def _draw_block(draw: ImageDraw.ImageDraw, text: str, img_w: int, img_h: int,
                position: str) -> None:
    plan = _layout(text, draw, img_w, img_h)
    block_h = sum(placed.height for placed in plan)

    # Крайняя строка блока садится вплотную к краю кадра: ни над её обводкой,
    # ни под ней ничего не остаётся. Отмеряем от реальных границ глифов, а не
    # от кегля — иначе строка без выносных элементов висела бы в пустоте.
    if position == "top":
        first = plan[0]
        above = (int(img_h * TOP_MARGIN_RATIO) + _stroke_width(first.size)
                 + _ink_bounds(first.text, first.size)[0])
        y = above - _font(first.size).getmetrics()[0]
    else:
        last = plan[-1]
        below = (int(img_h * BOTTOM_MARGIN_RATIO) + _stroke_width(last.size)
                 + _ink_bounds(last.text, last.size)[1])
        y = img_h - below - _font(last.size).getmetrics()[0] - (block_h - last.height)

    for placed in plan:
        # у каждой строки свой кегль, а значит и своя базовая линия с обводкой
        ascent = _font(placed.size).getmetrics()[0]
        _draw_line(draw, placed.text, placed.size, img_w / 2, y + ascent,
                   _stroke_width(placed.size))
        y += placed.height


def make_advice(image_bytes: bytes, top: str = "", bottom: str = "") -> bytes:
    """Возвращает JPEG-байты картинки с верхним и нижним текстом."""
    img = Image.open(io.BytesIO(image_bytes))
    img = img.convert("RGB")

    # нормализуем размер, чтобы текст на маленьких картинках не превращался в кашу
    side = max(img.size)
    if side > MAX_SIDE:
        img = _resize(img, MAX_SIDE / side)
    elif side < MIN_SIDE:
        img = _resize(img, MIN_SIDE / side)

    draw = ImageDraw.Draw(img)
    w, h = img.size

    if top.strip():
        _draw_block(draw, _prepare(top), w, h, "top")
    if bottom.strip():
        _draw_block(draw, _prepare(bottom), w, h, "bottom")

    out = io.BytesIO()
    img.save(out, format="JPEG", quality=92)
    return out.getvalue()


def _prepare(text: str) -> str:
    """Чистит невидимые символы из телеграма. Регистр остаётся авторским.

    ZWJ и селекторы начертания не трогаем: на них держатся составные эмодзи
    вроде 👨‍👩‍👧 и 👍🏽.
    """
    return text.translate(_CLEANUP).strip()


def _resize(img: Image.Image, factor: float) -> Image.Image:
    return img.resize(
        (max(1, round(img.width * factor)), max(1, round(img.height * factor))),
        Image.LANCZOS,
    )
