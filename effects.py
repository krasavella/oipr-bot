"""Порча картинок: жмых, шакал, зеркала и струя.

Всё сводится к одному: взять байты картинки и вернуть байты картинки.
Тяжёлый тут только жмых — настоящий seam carving, поэтому он режет швы на
уменьшенной копии, а результат растягивает обратно.
"""

from __future__ import annotations

import io
import random
from collections import namedtuple
from pathlib import Path

import numpy as np
from PIL import Image, ImageChops, ImageDraw, ImageEnhance, ImageFilter

MAX_SIDE = 1280     # больше телеграму всё равно незачем
MIN_SIDE = 400      # мельче — эффекты просто не видно
CARVE_SIDE = 320    # на таком размере швы считаются за доли секунды

MIN_STRENGTH = 1
MAX_STRENGTH = 10
DEFAULT_STRENGTH = 5

LUMA = np.array([0.299, 0.587, 0.114], dtype=np.float32)


def clamp_strength(value: int | None) -> int:
    if value is None:
        return DEFAULT_STRENGTH
    return max(MIN_STRENGTH, min(MAX_STRENGTH, value))


def _load(image_bytes: bytes) -> Image.Image:
    """Открывает картинку и приводит её к вменяемому размеру."""
    img = Image.open(io.BytesIO(image_bytes)).convert("RGB")
    side = max(img.size)
    if side > MAX_SIDE:
        factor = MAX_SIDE / side
    elif side < MIN_SIDE:
        factor = MIN_SIDE / side
    else:
        return img
    return img.resize(
        (max(1, round(img.width * factor)), max(1, round(img.height * factor))),
        Image.LANCZOS,
    )


def _encode(img: Image.Image, quality: int = 92) -> bytes:
    out = io.BytesIO()
    img.save(out, format="JPEG", quality=quality)
    return out.getvalue()


# --------------------------------------------------------------------------
# /cas — жмых: content-aware scale
# --------------------------------------------------------------------------

def _energy(gray: np.ndarray) -> np.ndarray:
    """Насколько пиксель отличается от соседей — по нему ищется шов."""
    dx = np.abs(np.diff(gray, axis=1, prepend=gray[:, :1]))
    dy = np.abs(np.diff(gray, axis=0, prepend=gray[:1, :]))
    return dx + dy


def _seam(energy: np.ndarray) -> np.ndarray:
    """Столбцы самого дешёвого вертикального шва, по одному на строку.

    Классическая динамика: сверху вниз накапливаем стоимость пути, снизу вверх
    его восстанавливаем. Матрицу переходов не храним — на каждом шаге хватает
    трёх соседей в строке выше.
    """
    h, w = energy.shape
    acc = energy.astype(np.float32, copy=True)
    left = np.empty(w, dtype=np.float32)
    right = np.empty(w, dtype=np.float32)
    for i in range(1, h):
        prev = acc[i - 1]
        left[0] = np.inf
        left[1:] = prev[:-1]
        right[-1] = np.inf
        right[:-1] = prev[1:]
        acc[i] += np.minimum(np.minimum(left, prev), right)

    seam = np.empty(h, dtype=np.intp)
    col = int(np.argmin(acc[-1]))
    seam[-1] = col
    for i in range(h - 2, -1, -1):
        lo = max(0, col - 1)
        col = lo + int(np.argmin(acc[i, lo:col + 2]))
        seam[i] = col
    return seam


def _carve(pixels: np.ndarray, count: int) -> np.ndarray:
    """Убирает count вертикальных швов, по одному за проход."""
    for _ in range(count):
        h, w = pixels.shape[:2]
        if w <= 3:
            break
        gray = pixels.astype(np.float32) @ LUMA
        keep = np.ones((h, w), dtype=bool)
        keep[np.arange(h), _seam(_energy(gray))] = False
        pixels = pixels[keep].reshape(h, w - 1, 3)
    return pixels


def squish(image_bytes: bytes, strength: int = DEFAULT_STRENGTH) -> bytes:
    """Жмых: выбрасывает малозначимые швы и растягивает остаток обратно.

    Фон схлопывается, а лица и объекты остаются на месте и слипаются друг с
    другом — тот самый эффект из тиктоковских мемов.
    """
    img = _load(image_bytes)
    size = img.size

    work = img
    side = max(work.size)
    if side > CARVE_SIDE:
        factor = CARVE_SIDE / side
        work = work.resize(
            (max(4, round(work.width * factor)), max(4, round(work.height * factor))),
            Image.LANCZOS,
        )

    share = 0.08 + 0.06 * clamp_strength(strength)  # 0.14 … 0.68 ширины и высоты
    pixels = np.asarray(work, dtype=np.uint8)
    pixels = _carve(pixels, int(pixels.shape[1] * share))
    # горизонтальные швы — та же задача для повёрнутой картинки
    pixels = _carve(pixels.swapaxes(0, 1), int(pixels.shape[0] * share)).swapaxes(0, 1)

    return _encode(Image.fromarray(pixels).resize(size, Image.LANCZOS))


# --------------------------------------------------------------------------
# /jpeg — шакал
# --------------------------------------------------------------------------

def deepfry(image_bytes: bytes, strength: int = DEFAULT_STRENGTH) -> bytes:
    """Шакал: несколько кругов пересжатия с задранными цветом и резкостью.

    Одного сохранения в плохом качестве мало — jpeg почти не портится дальше
    от повторного сжатия. Портит его как раз то, что между кругами картинку
    подкручивают: артефакты каждый раз считаются деталями и усиливаются.
    """
    strength = clamp_strength(strength)
    img = _load(image_bytes)
    size = img.size

    scale = 1 - 0.075 * strength  # 0.925 … 0.25
    frame = img.resize(
        (max(16, round(size[0] * scale)), max(16, round(size[1] * scale))),
        Image.LANCZOS,
    )

    quality = max(3, round(28 - 2.4 * strength))  # 26 … 4
    for _ in range(2 + strength // 2):
        frame = ImageEnhance.Color(frame).enhance(1.18)
        frame = ImageEnhance.Contrast(frame).enhance(1.12)
        frame = ImageEnhance.Sharpness(frame).enhance(1.7)
        buf = io.BytesIO()
        frame.save(buf, format="JPEG", quality=quality, subsampling=2)
        frame = Image.open(buf).convert("RGB")

    # обратно к исходному размеру ближайшим соседом — чтобы блоки 8×8 было видно
    return _encode(frame.resize(size, Image.NEAREST), quality=quality)


# --------------------------------------------------------------------------
# /kek и /keklol — зеркала
# --------------------------------------------------------------------------

def mirror(image_bytes: bytes, keep: str = "left") -> bytes:
    """Отзеркаливает половину картинки на вторую.

    keep="left" — левая половина уезжает направо, keep="right" — наоборот.
    У нечётной ширины центральный столбец достаётся обеим половинам.
    """
    img = _load(image_bytes)
    w, h = img.size
    half = (w + 1) // 2

    if keep == "left":
        source = img.crop((0, 0, half, h))
        img.paste(source.transpose(Image.FLIP_LEFT_RIGHT), (w - half, 0))
    else:
        source = img.crop((w - half, 0, w, h))
        img.paste(source.transpose(Image.FLIP_LEFT_RIGHT), (0, 0))

    return _encode(img)


# --------------------------------------------------------------------------
# /обоссать — жёлтая струя поверх картинки
# --------------------------------------------------------------------------

PRESET_DIR = Path(__file__).parent
PRESETS = tuple(f"о{i}.jpg" for i in range(1, 8))  # «о» здесь кириллическая
RARE_PRESET = "о8.jpg"       # выпадает сам по себе и номером не вызывается
RARE_CHANCE = 0.01           # раз на сотню поливов

PISS_COLOR = (255, 222, 0)   # цвет маркера на пресетах
SALIENCY_SIDE = 96           # на такой сетке ищется цель — мельче деталей и не надо
SKIN_WEIGHT = 1.6            # насколько кожа перевешивает обычную заметность
SPLASH_COVER = 1.15          # брызги чуть шире найденной цели
MIN_SPLASH = 0.22            # …но не меньше и не больше этой доли меньшей стороны
MAX_SPLASH = 0.46
EDGE_BAND = 6                # полоска вдоль края, по которой ищутся выходы струи

# mask — штрихи пресета, splash — точка попадания в них, radius — размер брызг,
# tails — выходы струи за кадр: (x, y, толщина штриха)
Stream = namedtuple("Stream", "mask splash radius tails")

_streams: dict[str, Stream] = {}


def _blur(values: np.ndarray, radius: float) -> np.ndarray:
    """Гауссово размытие карты чисел через PIL: тот работает только с байтами."""
    peak = float(values.max()) or 1.0
    small = Image.fromarray((values / peak * 255).astype(np.uint8))
    blurred = small.filter(ImageFilter.GaussianBlur(radius))
    return np.asarray(blurred, dtype=np.float32) / 255 * peak


def _mean3(values: np.ndarray) -> np.ndarray:
    """Среднее по 3×3 — им спектральный остаток сглаживает лог-амплитуду."""
    pad = np.pad(values, 1, mode="edge")
    h, w = values.shape
    return sum(pad[i:i + h, j:j + w] for i in range(3) for j in range(3)) / 9


def _hot_radius(values: np.ndarray, share: float) -> float:
    """Радиус ядра карты: круг такой же площади, как область выше порога."""
    hot = int((values > share * values.max()).sum())
    return float(np.sqrt(max(hot, 1) / np.pi))


# --- пресет: где брызги и куда уходит струя -------------------------------

def _runs(flags: np.ndarray) -> list[tuple[int, int]]:
    """Границы непрерывных участков в булевом ряду."""
    edges = np.r_[False, flags, False].astype(np.int8)
    starts = np.flatnonzero(np.diff(edges) > 0)
    ends = np.flatnonzero(np.diff(edges) < 0)
    return list(zip(starts.tolist(), ends.tolist()))


def _tails(ink: np.ndarray, radius: float,
           box: tuple[int, int, int, int]) -> tuple[tuple[int, int, int], ...]:
    """Места, где струя уходит за край пресета, — значит, приходит из-за кадра.

    Ищутся они по краю самого фото, а не по обрезке рисунка: у обрезки все
    четыре стороны заведомо заняты крайними штрихами, но кончик, оборванный
    посреди кадра, никакая струя не продолжает.

    Хвост струи пересекает край одним штрихом и оставляет на нём считанные
    пиксели. Обрезанные краем брызги — совсем другое дело: они упираются в
    край десятками пикселей сразу, и продолжать их наружу нечего. Порог по
    размеру брызг эти два случая и разделяет.
    """
    height, width = ink.shape
    borders = {
        "left": (ink[:, :EDGE_BAND].any(axis=1), lambda p: (0, p)),
        "right": (ink[:, -EDGE_BAND:].any(axis=1), lambda p: (width - 1, p)),
        "top": (ink[:EDGE_BAND, :].any(axis=0), lambda p: (p, 0)),
        "bottom": (ink[-EDGE_BAND:, :].any(axis=0), lambda p: (p, height - 1)),
    }

    found = []
    for flags, place in borders.values():
        if not flags.any() or flags.sum() > 0.5 * radius:
            continue
        for start, end in _runs(flags):
            x, y = place((start + end) // 2)
            found.append((x - box[0], y - box[1], end - start))
    return tuple(found)


def _extract_stream(path: Path) -> Stream:
    """Разбирает пресет: маска штрихов, точка попадания и выходы струи за край.

    Маркер на всех пресетах один и тот же: очень яркий и без синего. По этим
    двум признакам он отделяется от фона, даже если на фото жёлтая листва —
    она заметно темнее и синее. Остатки листвы добивает срез по 0.45: слабые
    пиксели уходят в ноль. Дальше штрихи чуть раздуваются, чтобы тонкие линии
    не рассыпались на точки при увеличении.

    Брызги — самое плотное место рисунка: струя к ним идёт одной линией, а на
    месте попадания маркер намотан в клубок. Плотность считается по сильно
    размытой маске, её вершина и есть точка попадания.
    """
    pixels = np.asarray(Image.open(path).convert("RGB"), dtype=np.int16)
    r, g, b = pixels[..., 0], pixels[..., 1], pixels[..., 2]
    base = np.minimum(r, g).astype(np.float32)
    alpha = np.clip((base - b - 70) / 45, 0, 1) * np.clip((base - 150) / 45, 0, 1)
    alpha = np.clip((alpha - 0.45) / 0.35, 0, 1)

    mask = Image.fromarray((alpha * 255).astype(np.uint8))
    mask = mask.filter(ImageFilter.MaxFilter(3)).filter(ImageFilter.GaussianBlur(0.7))

    ink = np.asarray(mask) > 128
    box = mask.getbbox()
    mask = mask.crop(box)

    density = _blur(np.asarray(mask, dtype=np.float32), max(mask.size) / 22)
    y, x = np.unravel_index(int(np.argmax(density)), density.shape)
    radius = _hot_radius(density, 0.5)
    return Stream(mask, (int(x), int(y)), radius, _tails(ink, radius, box))


def _stream(name: str) -> Stream:
    """Разбор пресета; считается один раз на всё время жизни бота."""
    if name not in _streams:
        _streams[name] = _extract_stream(PRESET_DIR / name)
    return _streams[name]


# --- картинка: куда целиться ----------------------------------------------

def _saliency(img: Image.Image) -> np.ndarray:
    """Карта заметности по спектральному остатку (Hou & Zhang, 2007).

    Всё предсказуемое в картинке — это то, что усреднённо есть в её спектре;
    вычтя из лог-амплитуды её же сглаженную копию, остаёшься с тем, что из
    общего ряда выбивается. Обратное преобразование собирает эти остатки
    обратно в карту, где горит необычное: объект на фоне, а не сам фон.
    """
    small = img.convert("L").resize((SALIENCY_SIDE, SALIENCY_SIDE), Image.LANCZOS)
    spectrum = np.fft.fft2(np.asarray(small, dtype=np.float32))
    log_amp = np.log(np.abs(spectrum) + 1e-6)
    residual = log_amp - _mean3(log_amp)
    restored = np.fft.ifft2(np.exp(residual + 1j * np.angle(spectrum)))
    return _blur(np.abs(restored) ** 2, 3)


def _skin(img: Image.Image) -> np.ndarray:
    """Где в кадре лицо: кожа, взвешенная детализацией.

    Границы кожи взяты в YCbCr, а не в RGB: там оттенок кожи почти не зависит
    от яркости, поэтому одни и те же пороги ловят и светлую, и тёмную. Одной
    кожи, впрочем, мало — голое плечо её даёт столько же, сколько лицо. Лицо
    отличает мелкая деталь: глаза, ноздри, губы, брови. Поэтому кожа умножается
    на перепад яркости по соседям, и гладкие участки уходят вниз.
    """
    small = img.convert("YCbCr").resize((SALIENCY_SIDE, SALIENCY_SIDE), Image.LANCZOS)
    y, cb, cr = np.asarray(small, dtype=np.int16).transpose(2, 0, 1)
    skin = (y > 55) & (77 <= cb) & (cb <= 130) & (133 <= cr) & (cr <= 175)

    detail = _blur(_energy(y.astype(np.float32)), 2)
    detail /= detail.max() or 1.0
    return _blur(skin.astype(np.float32) * (0.35 + detail), 2)


def _target(img: Image.Image) -> tuple[int, int, float]:
    """Куда целиться и какого размера должны быть брызги.

    Заметность подсказывает, где в кадре главное, кожа поднимает лицо над
    остальным, а окно по краям не даёт целиться в самый угол — иначе половина
    брызг окажется за кадром. Размер цели берётся из площади её пятна: по
    мелкому лицу бьём мелко, по крупному — крупно.
    """
    score = _saliency(img)
    score /= score.max() or 1.0
    score += SKIN_WEIGHT * _skin(img)

    window = np.hanning(SALIENCY_SIDE) ** 0.35
    score *= np.outer(window, window)
    if not score.any():  # ровная заливка без единой зацепки — бьём в середину
        return img.width // 2, img.height // 2, MIN_SPLASH * min(img.size)

    y, x = np.unravel_index(int(np.argmax(score)), score.shape)
    scale = min(img.size) / SALIENCY_SIDE
    radius = _hot_radius(score, 0.6) * scale
    radius = min(max(radius * SPLASH_COVER, MIN_SPLASH * min(img.size)),
                 MAX_SPLASH * min(img.size))
    return round((x + 0.5) * img.width / SALIENCY_SIDE), \
        round((y + 0.5) * img.height / SALIENCY_SIDE), radius


# --- собственно эффект ----------------------------------------------------

def _stretch(stream: Stream, size: tuple[int, int], zoom: float,
             offset: tuple[int, int]) -> Image.Image:
    """Дотягивает хвосты струи до края кадра и дальше.

    Пресет ложится по размеру брызг, и его собственный край почти никогда не
    совпадает с краем картинки: струя тогда начиналась бы в воздухе посреди
    кадра. Поэтому каждый хвост продолжается прямой за границу — в ту сторону,
    куда он шёл от брызг. Штрихи мягкие по краям, поэтому продолжение слегка
    размывается, иначе стык видно.
    """
    far = size[0] + size[1]
    extension = Image.new("L", size, 0)
    pen = ImageDraw.Draw(extension)
    for x, y, thickness in stream.tails:
        start = (offset[0] + x * zoom, offset[1] + y * zoom)
        way = (x - stream.splash[0], y - stream.splash[1])
        length = np.hypot(*way) or 1.0
        pen.line(
            [start, (start[0] + way[0] / length * far,
                     start[1] + way[1] / length * far)],
            fill=255, width=max(2, round(thickness * zoom)),
        )
    return extension.filter(ImageFilter.GaussianBlur(0.8))


def _pick_preset(variant: int | None) -> str:
    """Какой пресет лить: по номеру, а без номера — случайный или редкий."""
    if variant:
        return PRESETS[(variant - 1) % len(PRESETS)]
    if random.random() < RARE_CHANCE:
        return RARE_PRESET
    return random.choice(PRESETS)


def piss(image_bytes: bytes, variant: int | None = None) -> bytes:
    """Обоссать: накладывает на картинку жёлтую струю с одного из пресетов.

    Струя сама наводится на главное в кадре — обычно на лицо, — подгоняет
    размер брызг под размер цели и приходит из-за края картинки.

    Номером берётся строго обычный пресет: редкий в этот список не входит и
    достаётся только тому, кто позвал команду без числа и кому повезло.
    """
    img = _load(image_bytes)
    name = _pick_preset(variant)
    stream = _stream(name)

    x, y, radius = _target(img)
    zoom = radius / max(stream.radius, 1.0)
    mask = stream.mask.resize(
        (max(1, round(stream.mask.width * zoom)),
         max(1, round(stream.mask.height * zoom))),
        Image.LANCZOS,
    )

    # рисунок ложится брызгами точно в цель, всё остальное уходит куда придётся,
    # хоть и за кадр
    offset = (x - round(stream.splash[0] * zoom), y - round(stream.splash[1] * zoom))
    canvas = Image.new("L", img.size, 0)
    canvas.paste(mask, offset)
    canvas = ImageChops.lighter(canvas, _stretch(stream, img.size, zoom, offset))

    img.paste(Image.new("RGB", img.size, PISS_COLOR), (0, 0), canvas)
    return _encode(img)
