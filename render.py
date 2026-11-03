"""PIL-based card rendering for the haidou (ARAM Hextech) plugin."""

import math
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

FONT_DIR = Path(__file__).parent / "fonts"
FONT_REG = str(FONT_DIR / "NotoSansHans-Regular.otf")
FONT_MED = str(FONT_DIR / "NotoSansHans-Medium.otf")

BG = (10, 14, 20)
PANEL = (19, 26, 36)
PANEL_LIGHT = (28, 37, 50)
GOLD = (245, 210, 122)
TEXT = (232, 232, 232)
DIM = (143, 150, 160)
GREEN = (91, 201, 122)
RED = (232, 106, 106)

TIER_COLORS = {
    1: (240, 169, 59),
    2: (199, 125, 232),
    3: (90, 167, 232),
    4: (91, 201, 122),
    5: (143, 150, 160),
}
TIER_NAMES = {1: "T1", 2: "T2", 3: "T3", 4: "T4", 5: "T5"}
GRADE_COLORS = {
    "S": (240, 169, 59),
    "A": (199, 125, 232),
    "B": (90, 167, 232),
    "C": (91, 201, 122),
    "D": (143, 150, 160),
}
LEVEL_COLORS = {
    "棱彩": (240, 169, 59),
    "黄金": (222, 197, 108),
    "白银": (170, 180, 195),
}


def _font(size: int, medium: bool = False) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(FONT_MED if medium else FONT_REG, size)


def _new_canvas(width: int, height: int) -> tuple[Image.Image, ImageDraw.ImageDraw]:
    img = Image.new("RGB", (width, height), BG)
    return img, ImageDraw.Draw(img)


def _finish(img: Image.Image, out_path: Path) -> Path:
    img.save(out_path, "PNG")
    return out_path


def _placeholder_icon(size: int) -> Image.Image:
    img = Image.new("RGB", (size, size), PANEL_LIGHT)
    d = ImageDraw.Draw(img)
    d.rectangle([0, 0, size - 1, size - 1], outline=PANEL, width=2)
    return img


def _load_icon(path: Path | None, size: int) -> Image.Image:
    if path and Path(path).exists():
        try:
            icon = Image.open(path).convert("RGB").resize((size, size))
            return icon
        except Exception:
            pass
    return _placeholder_icon(size)


def _paste_icon(img: Image.Image, icon: Image.Image, x: int, y: int, size: int):
    img.paste(icon, (x, y))
    ImageDraw.Draw(img).rectangle(
        [x, y, x + size - 1, y + size - 1], outline=(60, 70, 85), width=1
    )


def _draw_badge(
    d: ImageDraw.ImageDraw,
    x: int,
    y: int,
    text: str,
    color,
    w: int = 34,
    h: int = 24,
    size: int = 16,
) -> int:
    d.rounded_rectangle([x, y, x + w, y + h], radius=4, fill=color)
    f = _font(size, medium=True)
    tw = d.textlength(text, font=f)
    d.text((x + (w - tw) / 2, y + (h - size) / 2 - 1), text, font=f, fill=(15, 18, 24))
    return w


def _hero_display_name(meta: dict) -> str:
    return meta.get("title") or meta.get("name") or "?"


def _trend_color(trend: str):
    return GREEN if trend == "上升" else RED if trend == "下降" else DIM


def _trend_char(trend: str) -> str:
    return "升" if trend == "上升" else "降" if trend == "下降" else "稳"


def _wrap_footer(d: ImageDraw.ImageDraw, img: Image.Image, text: str):
    f = _font(18)
    tw = d.textlength(text, font=f)
    d.text(((img.width - tw) / 2, img.height - 34), text, font=f, fill=DIM)


def _win_color(win: float):
    if win >= 0.545:
        return GOLD
    if win >= 0.515:
        return GREEN
    if win >= 0.485:
        return TEXT
    return DIM


def render_rank_card(heroes: list, hero_meta: dict, date_str: str, out: Path) -> Path:
    """Official rank order grid, 5 heroes per row."""
    cols = 4
    cell_w = 216
    cell_h = 34
    W = 940
    header_h = 92
    footer_h = 48
    H = header_h + math.ceil(len(heroes) / cols) * cell_h + 20 + footer_h

    img, d = _new_canvas(W, H)
    d.text(
        (36, 22), "海克斯大乱斗 · 英雄胜率榜", font=_font(34, medium=True), fill=GOLD
    )
    d.text(
        (36, 66),
        f"官方排名第 1~{len(heroes)} 位 · 统计日期 {date_str}（数据源：掌盟）",
        font=_font(18),
        fill=DIM,
    )

    y = header_h
    for i, h in enumerate(heroes):
        cx = 36 + (i % cols) * cell_w
        cy = y + (i // cols) * cell_h
        meta = hero_meta.get(h["champ_id"], {})
        raw_name = _hero_display_name(meta)
        name = raw_name[:4] + "…" if len(raw_name) > 4 else raw_name
        d.text((cx, cy), f"{h['rank']}", font=_font(20), fill=DIM)
        d.text((cx + 34, cy), name, font=_font(20), fill=TEXT)
        pct = f"{h['win'] * 100:.1f}%"
        pw = d.textlength(pct, font=_font(20))
        d.text(
            (cx + cell_w - pw - 4, cy), pct, font=_font(20), fill=_win_color(h["win"])
        )
    y += math.ceil(len(heroes) / cols) * cell_h + 20

    _wrap_footer(d, img, "发送 /海斗 英雄名 可查看单个英雄的海克斯适配榜")
    return _finish(img, out)


def render_augment_card(
    rows: list, icons: list, hero_meta: dict, date_str: str, title: str, out: Path
) -> Path:
    """Augment strength list with icons and best-heroes."""
    row_h = 58
    W = 940
    H = 92 + len(rows) * row_h + 18 + 48
    img, d = _new_canvas(W, H)
    d.text((36, 22), f"海克斯大乱斗 · {title}", font=_font(32, medium=True), fill=GOLD)
    d.text((36, 64), f"按胜率排序 · 统计日期 {date_str}", font=_font(18), fill=DIM)

    y = 92
    f_main = _font(22, medium=True)
    f_sub = _font(19)
    for i, ((aug, meta), icon) in enumerate(zip(rows, icons)):
        if i % 2 == 0:
            d.rectangle([24, y, W - 24, y + row_h - 8], fill=PANEL)
        d.text((36, y + 12), f"{i + 1}", font=f_main, fill=DIM)
        _paste_icon(img, _load_icon(icon, 40), 76, y + 4, 40)
        name = meta.get("name_cn") or f"未知强化#{aug['aug_id']}"
        d.text((128, y + 12), name[:9], font=f_main, fill=TEXT)

        level_cn = {"kPrismatic": "棱彩", "kGold": "黄金", "kSilver": "白银"}.get(
            meta.get("level", ""), ""
        )
        if level_cn:
            lx = 128 + d.textlength(name[:9], font=f_main) + 12
            _draw_badge(
                d,
                lx,
                y + 11,
                level_cn,
                LEVEL_COLORS.get(level_cn, DIM),
                w=52,
                h=26,
                size=15,
            )

        win = f"{aug['win'] * 100:.1f}%"
        pick = f"{aug['pick'] * 100:.1f}%"
        d.text((430, y + 12), win, font=f_main, fill=GOLD)
        d.text((520, y + 14), "胜率", font=_font(15), fill=DIM)
        d.text((610, y + 12), pick, font=f_main, fill=TEXT)
        d.text((700, y + 14), "选取", font=_font(15), fill=DIM)
        best = " ".join(
            _hero_display_name(hero_meta.get(cid, {})) for cid in aug["best_heroes"]
        )
        d.text((780, y + 15), f"适配 {best}".strip()[:14], font=f_sub, fill=DIM)
        y += row_h

    _wrap_footer(d, img, "发送 /海斗 英雄名 可查看指定英雄的强化适配")
    return _finish(img, out)


def _wrap_text(d: ImageDraw.ImageDraw, text: str, font, max_w: int) -> list[str]:
    lines, cur = [], ""
    for ch in text:
        if d.textlength(cur + ch, font=font) > max_w:
            lines.append(cur)
            cur = ch
        else:
            cur += ch
    if cur:
        lines.append(cur)
    return lines


def render_augment_detail(
    stat: dict,
    meta: dict,
    best: list,
    avg_win: float,
    win_rank: int,
    total: int,
    hero_meta: dict,
    date_str: str,
    icons: list,
    out: Path,
) -> Path:
    """Single augment: overall strength vs average + best-fit heroes."""
    W = 940
    f_desc = _font(20)
    desc = meta.get("tooltip", "") or meta.get("desc", "")
    desc = desc.replace("@Stacks_Max@", "满").replace("&nbsp;", " ")
    measure = _new_canvas(W, 10)[1]
    desc_lines = _wrap_text(measure, desc, f_desc, W - 76) if desc else []
    best_rows = max(1, math.ceil(len(best) / 3))
    H = 150 + len(desc_lines) * 30 + 24 + 118 + 46 + best_rows * 66 + 60
    img, d = _new_canvas(W, H)

    # header: icon + names
    main_icon = _load_icon(icons[0], 110) if icons else _placeholder_icon(110)
    _paste_icon(img, main_icon, 36, 26, 110)
    name = meta.get("name_cn") or f"未知强化#{stat['aug_id']}"
    d.text((170, 30), name, font=_font(32, medium=True), fill=GOLD)
    nx = 170 + d.textlength(name, font=_font(32, medium=True)) + 16
    level_cn = {"kPrismatic": "棱彩", "kGold": "黄金", "kSilver": "白银"}.get(
        meta.get("level", ""), ""
    )
    if level_cn:
        _draw_badge(
            d, nx, 38, level_cn, LEVEL_COLORS.get(level_cn, DIM), w=56, h=28, size=16
        )
    en = meta.get("name_en", "")
    d.text((172, 76), en, font=_font(16), fill=DIM)
    d.text((172, 102), f"统计日期 {date_str}", font=_font(17), fill=DIM)

    # description
    y = 152
    for i, line in enumerate(desc_lines):
        d.text((38, y + i * 30), line, font=f_desc, fill=TEXT)
    y += len(desc_lines) * 30 + 24

    # stats panel
    d.rounded_rectangle([36, y, W - 36, y + 106], radius=10, fill=PANEL)
    diff = stat["win"] - avg_win
    cmp_txt = f"{'高于' if diff >= 0 else '低于'}平均 {abs(diff) * 100:.1f}%"
    d.text(
        (64, y + 14),
        f"{stat['win'] * 100:.1f}%",
        font=_font(40, medium=True),
        fill=_win_color(stat["win"]),
    )
    d.text((64, y + 66), cmp_txt, font=_font(18), fill=GREEN if diff >= 0 else RED)
    d.text(
        (330, y + 20),
        f"胜率排名 {win_rank} / {total}",
        font=_font(24, medium=True),
        fill=TEXT,
    )
    d.text((330, y + 62), "全体海克斯按胜率排序", font=_font(17), fill=DIM)
    d.text(
        (640, y + 20),
        f"{stat['pick'] * 100:.1f}%",
        font=_font(28, medium=True),
        fill=TEXT,
    )
    d.text((640, y + 66), "选取率", font=_font(17), fill=DIM)
    y += 106 + 24

    # best-fit heroes
    d.text((36, y), "最适配英雄", font=_font(24, medium=True), fill=GOLD)
    d.line([36, y + 36, W - 36, y + 36], fill=(40, 50, 64), width=1)
    y += 46
    cell_w = (W - 72) // 3
    for i, ((hm, hw), icon) in enumerate(zip(best, icons[1:])):
        cx = 36 + (i % 3) * cell_w
        cy = y + (i // 3) * 66
        _paste_icon(img, _load_icon(icon, 44), cx, cy, 44)
        hname = _hero_display_name(hm)[:6] if hm else "?"
        d.text((cx + 54, cy - 1), hname, font=_font(21, medium=True), fill=TEXT)
        if hw is not None:
            d.text(
                (cx + 54, cy + 26),
                f"整体胜率 {hw * 100:.1f}%",
                font=_font(16),
                fill=GOLD,
            )

    _wrap_footer(d, img, "发送 /海斗 英雄名 查看英雄视角的海克斯适配榜")
    return _finish(img, out)


def render_hero_card(
    hero: dict,
    rank: dict | None,
    detail: dict,
    aug_rows: list,
    aug_icons: list,
    aug_meta: dict,
    hero_meta: dict,
    portrait: Path | None,
    date_str: str,
    out: Path,
) -> Path:
    """Single hero: portrait, win/pick stats, augment fit list, best partners."""
    W = 940
    top_h = 170
    row_h = 52
    n_augs = len(aug_rows)
    partner_h = 66
    H = top_h + 46 + n_augs * row_h + partner_h + 66
    img, d = _new_canvas(W, H)

    name = hero.get("title") or hero.get("name") or "?"
    full = f"{hero.get('name', '')} · {name}"

    _paste_icon(img, _load_icon(portrait, 130), 36, 28, 130)
    d.text((190, 30), full, font=_font(30, medium=True), fill=TEXT)
    if rank:
        color = _win_color(rank["win"])
        _draw_badge(d, 190, 76, f"NO.{rank['rank']}", color, w=76, h=28, size=16)
        d.text((280, 78), f"胜率 {rank['win'] * 100:.1f}%", font=_font(22), fill=GOLD)
        d.text(
            (450, 78), f"登场率 {rank['pick'] * 100:.1f}%", font=_font(22), fill=TEXT
        )
        d.text(
            (670, 78),
            f"趋势 {rank['trend']}",
            font=_font(22),
            fill=_trend_color(rank["trend"]),
        )
        d.text((190, 112), f"统计日期 {date_str}", font=_font(17), fill=DIM)
    else:
        d.text((190, 80), "暂无该英雄的榜单数据", font=_font(20), fill=DIM)

    # augment fit section
    y = top_h
    d.text((36, y), "海克斯适配 TOP", font=_font(24, medium=True), fill=GOLD)
    d.line([36, y + 36, W - 36, y + 36], fill=(40, 50, 64), width=1)
    y += 46
    for i, (a, icon) in enumerate(zip(aug_rows, aug_icons)):
        meta = aug_meta.get(a["aug_id"], {})
        aname = meta.get("name_cn") or f"未知#{a['aug_id']}"
        if i % 2 == 0:
            d.rectangle([24, y - 4, W - 24, y + row_h - 14], fill=PANEL)
        d.text((36, y), f"{i + 1}", font=_font(21), fill=DIM)
        _paste_icon(img, _load_icon(icon, 38), 80, y - 4, 38)
        d.text((130, y), aname[:11], font=_font(21, medium=True), fill=TEXT)
        gx = 130 + d.textlength(aname[:11], font=_font(21, medium=True)) + 14
        grade = a.get("grade", "")
        if grade:
            _draw_badge(
                d, gx, y - 1, grade, GRADE_COLORS.get(grade, DIM), w=30, h=24, size=16
            )
        level_cn = {"kPrismatic": "棱彩", "kGold": "黄金", "kSilver": "白银"}.get(
            meta.get("level", ""), ""
        )
        if level_cn:
            lx = gx + 44
            d.text(
                (lx, y + 2),
                level_cn,
                font=_font(17),
                fill=LEVEL_COLORS.get(level_cn, DIM),
            )
        d.text((W - 160, y), f"选取 {a['pick'] * 100:.1f}%", font=_font(20), fill=TEXT)
        y += row_h

    # partners
    y += 8
    d.text((36, y), "最佳搭档", font=_font(22, medium=True), fill=GOLD)
    partners = detail.get("partners", [])[:5]
    if partners:
        text = "  ".join(
            f"{_hero_display_name(hero_meta.get(p['champ_id'], {}))} {p['win'] * 100:.1f}%"
            for p in partners
        )
        d.text((36, y + 34), text[:60], font=_font(19), fill=TEXT)
    return _finish(img, out)
