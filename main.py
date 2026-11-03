import asyncio
import json
import re
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import aiohttp

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.message_components import Image
from astrbot.api.star import Context, Star, register
from astrbot.core.config.astrbot_config import AstrBotConfig
from astrbot.core.utils.astrbot_path import get_astrbot_data_path

from .render import (
    render_augment_card,
    render_augment_detail,
    render_hero_card,
    render_rank_card,
)

BASE_API = "https://mlol.qt.qq.com/go/battle_info/odp_proxy"
HERO_LIST_URL = "https://game.gtimg.cn/images/lol/act/img/js/heroList/hero_list.js"
AUGMENT_META_URL = "https://game.gtimg.cn/images/lol/act/img/js/kiwi/kiwi_augments.json"

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"

LEVEL_CN = {"kPrismatic": "棱彩", "kGold": "黄金", "kSilver": "白银"}
TREND_CN = {"上升": "升", "下降": "降", "未变化": "稳"}
HERO_RECORD_RE = re.compile(
    r"^(\d+)_(\d+)_(未变化|上升|下降)(?:\d+位)?_(\d+(?:\.\d+)?)_(\d+(?:\.\d+)?)_(.*)$"
)
RANK_TTL = 2 * 3600
META_TTL = 24 * 3600
DETAIL_TTL = 12 * 3600


@register(
    "astrbot_plugin_haidou",
    "Matsuko",
    "英雄联盟海克斯大乱斗（海斗）查询：英雄胜率榜、单英雄海克斯适配榜、海克斯强度榜",
    "1.0.0",
)
class HaidouPlugin(Star):
    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.config = config
        self.data_dir = (
            Path(get_astrbot_data_path()) / "plugin_data" / "astrbot_plugin_haidou"
        )
        self.data_dir.mkdir(parents=True, exist_ok=True)
        (self.data_dir / "icons").mkdir(exist_ok=True)
        (self.data_dir / "cards").mkdir(exist_ok=True)
        self._session: aiohttp.ClientSession | None = None
        # cache: key -> (expire_ts, value)
        self._cache: dict[str, tuple[float, Any]] = {}

    async def terminate(self):
        if self._session and not self._session.closed:
            await self._session.close()

    # ---------------- http ----------------

    def _session_get(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=int(self.config.get("timeout", 25)))
            )
        return self._session

    async def _fetch_json(self, url: str) -> Any:
        async with self._session_get().get(url, headers={"User-Agent": UA}) as resp:
            resp.raise_for_status()
            return await resp.json(content_type=None)

    async def _cache_get(self, key: str, ttl: float):
        hit = self._cache.get(key)
        if hit and hit[0] > asyncio.get_event_loop().time():
            return hit[1]
        return None

    def _cache_set(self, key: str, value: Any, ttl: float):
        self._cache[key] = (asyncio.get_event_loop().time() + ttl, value)

    async def _download_icon(self, url: str) -> Path | None:
        if not url:
            return None
        name = url.rsplit("/", 1)[-1]
        path = self.data_dir / "icons" / name
        if path.exists() and path.stat().st_size > 0:
            return path
        try:
            async with self._session_get().get(url, headers={"User-Agent": UA}) as resp:
                if resp.status != 200:
                    return None
                path.write_bytes(await resp.read())
            return path
        except Exception as e:
            logger.warning(f"haidou: icon download failed {url}: {e}")
            return None

    # ---------------- meta ----------------

    async def _get_hero_meta(self) -> dict[int, dict]:
        cached = await self._cache_get("hero_meta", META_TTL)
        if cached is not None:
            return cached
        async with self._session_get().get(
            HERO_LIST_URL, headers={"User-Agent": UA}
        ) as resp:
            text = await resp.text()
        m = re.search(r"\[.*\]", text, re.S)
        if not m:
            raise RuntimeError("hero_list parse failed")
        heroes = {}
        for h in json.loads(m.group(0)):
            heroes[int(h["heroId"])] = h
        self._cache_set("hero_meta", heroes, META_TTL)
        return heroes

    async def _get_augment_meta(self) -> dict[int, dict]:
        cached = await self._cache_get("augment_meta", META_TTL)
        if cached is not None:
            return cached
        data = await self._fetch_json(AUGMENT_META_URL)
        augs = {}
        for a in data:
            if a.get("isPBE"):
                continue
            try:
                augs[int(a["augmentID"])] = a
            except (KeyError, ValueError):
                continue
        self._cache_set("augment_meta", augs, META_TTL)
        return augs

    # ---------------- rank data ----------------

    async def _get_hero_rank(self) -> tuple[str, list[dict]]:
        """Fetch hero win-rate list, falling back over older stat dates."""
        cached = await self._cache_get("hero_rank", RANK_TTL)
        if cached is not None:
            return cached
        today = date.today()
        for days_back in (1, 2, 3, 4):
            ds = (today - timedelta(days=days_back)).strftime("%Y%m%d")
            url = f"{BASE_API}/fuwen_aram_hero_rank_v2?dtstatdate={ds}"
            try:
                data = await self._fetch_json(url)
            except Exception as e:
                logger.warning(f"haidou: hero rank {ds} fetch failed: {e}")
                continue
            if data.get("code") != 0 or not data.get("data"):
                continue
            try:
                inner = json.loads(list(data["data"]["_fieldValues"].values())[0])
                heroes = self._parse_hero_rank(inner["listcollect"])
            except Exception as e:
                logger.warning(f"haidou: hero rank {ds} parse failed: {e}")
                continue
            if heroes:
                result = (ds, heroes)
                self._cache_set("hero_rank", result, RANK_TTL)
                return result
        raise RuntimeError("hero rank data unavailable in the last 4 days")

    @staticmethod
    def _parse_hero_rank(raw: str) -> list[dict]:
        heroes = []
        for rec in raw.split("#"):
            m = HERO_RECORD_RE.match(rec.strip())
            if not m:
                continue
            champ_id, rank_pos, trend, win, pick, rest = m.groups()
            tail = rest.split("_")
            augs = [a for a in tail[-1].split(",") if a.isdigit()] if tail else []
            heroes.append(
                {
                    "champ_id": int(champ_id),
                    "rank": int(rank_pos),
                    "trend": trend,
                    "win": float(win),
                    "pick": float(pick),
                    "top_augs": [int(a) for a in augs][:3],
                }
            )
        return heroes

    async def _get_rune_rank(self) -> tuple[str, list[dict]]:
        cached = await self._cache_get("rune_rank", RANK_TTL)
        if cached is not None:
            return cached
        url = f"{BASE_API}/fuwen_aram_rune_rank_v2?augmentid_level=255"
        data = await self._fetch_json(url)
        inner = json.loads(list(data["data"]["_fieldValues"].values())[0])
        augs = []
        for rec in inner["augmentlist"].split("#"):
            parts = rec.split("_")
            if len(parts) < 9:
                continue
            try:
                augs.append(
                    {
                        "aug_id": int(parts[0]),
                        "pick": float(parts[2]),
                        "win": float(parts[5]),
                        "best_heroes": [
                            int(x) for x in parts[8].split(",") if x.isdigit()
                        ][:3],
                    }
                )
            except (ValueError, IndexError):
                continue
        augs.sort(key=lambda x: x["win"], reverse=True)
        result = (str(inner.get("dtstatdate", "")), augs)
        self._cache_set("rune_rank", result, RANK_TTL)
        return result

    async def _get_hero_detail(self, champ_id: int) -> dict:
        cached = await self._cache_get(f"detail_{champ_id}", DETAIL_TTL)
        if cached is not None:
            return cached
        url = f"{BASE_API}/fuwen_hero_rank?championid={champ_id}"
        data = await self._fetch_json(url)
        if data.get("code") != 0 or not data.get("data"):
            raise RuntimeError(f"no detail data for hero {champ_id}")
        inner = json.loads(list(data["data"]["_fieldValues"].values())[0])
        detail = {"augs": [], "partners": []}
        irank = inner.get("augment_json_irank", "")
        if ":" in irank:
            for rec in irank.split(":", 1)[1].split("#"):
                parts = rec.split("|")
                if len(parts) >= 6:
                    detail["augs"].append(
                        {
                            "aug_id": int(parts[2]),
                            "pick": float(parts[4]),
                            "grade": parts[5],
                        }
                    )
        partners = inner.get("championid_json", "")
        for rec in partners.split("#"):
            parts = rec.split("|")
            if len(parts) >= 4:
                detail["partners"].append(
                    {
                        "champ_id": int(parts[0]),
                        "win": float(parts[1]),
                        "pick": float(parts[2]),
                    }
                )
        self._cache_set(f"detail_{champ_id}", detail, DETAIL_TTL)
        return detail

    # ---------------- helpers ----------------

    async def _find_hero(self, query: str) -> dict | None:
        meta = await self._get_hero_meta()
        q = query.strip()
        if not q:
            return None
        if q.isdigit() and int(q) in meta:
            return meta[int(q)]
        ql = q.lower()
        exact = []
        fuzzy = []
        for h in meta.values():
            raw_kw = h.get("keywords", "")
            kws = raw_kw.split(",") if isinstance(raw_kw, str) else list(raw_kw)
            keys = {h.get("name", ""), h.get("title", ""), h.get("alias", "").lower()}
            keys |= {k.strip().lower() for k in kws if k.strip()}
            if q in keys or ql in keys:
                exact.append(h)
            elif q in h.get("name", "") or q in h.get("title", ""):
                fuzzy.append(h)
        pool = exact or fuzzy
        return pool[0] if pool else None

    def _fmt_pct(self, v: float) -> str:
        return f"{v * 100:.1f}%"

    def _fmt_date(self, ds: str) -> str:
        try:
            return datetime.strptime(ds, "%Y%m%d").strftime("%m-%d")
        except ValueError:
            return ds

    async def _find_augment(self, query: str) -> tuple[dict, dict] | None:
        """Match an augment by id or name; returns (stat, meta) or None."""
        _, augs = await self._get_rune_rank()
        aug_meta = await self._get_augment_meta()
        q = query.strip()
        if not q:
            return None
        if q.isdigit():
            meta = aug_meta.get(int(q))
            if meta:
                stat = next((a for a in augs if a["aug_id"] == int(q)), None)
                if stat:
                    return stat, meta
        exact, fuzzy = [], []
        for a in augs:
            meta = aug_meta.get(a["aug_id"], {})
            name = meta.get("name_cn", "")
            en = meta.get("name_en", "").lower()
            if q == name:
                exact.append((a, meta))
            elif q in name or (q.lower() in en and q.isascii()):
                fuzzy.append((a, meta))
        pool = exact or fuzzy
        return pool[0] if pool else None

    # ---------------- commands ----------------

    @filter.command("海斗", alias={"海斗榜", "海克斯大乱斗"})
    async def haidou(self, event: AstrMessageEvent, arg1: str = "", arg2: str = ""):
        """海斗查询：/海斗 看英雄榜；/海斗 阿狸 查英雄；/海斗 海克斯 看强化榜"""
        try:
            if arg1 in ("海克斯", "强化", "海克斯榜"):
                yield event.chain_result(
                    [Image.fromFileSystem(str(await self._augment_view(arg2)))]
                )
            elif arg1:
                hero = await self._find_hero(arg1)
                if not hero:
                    yield event.plain_result(
                        f"没找到英雄「{arg1}」哦，试试完整名字或别名，比如：阿狸、亚索、ali"
                    )
                    return
                yield event.chain_result(
                    [Image.fromFileSystem(str(await self._hero_card(hero)))]
                )
            else:
                yield event.chain_result(
                    [Image.fromFileSystem(str(await self._rank_card()))]
                )
        except Exception as e:
            logger.error(f"haidou: {traceback_exc()}")
            yield event.plain_result(f"海斗数据查询失败了：{e}")

    @filter.command("海斗帮助")
    async def haidou_help(self, event: AstrMessageEvent):
        yield event.plain_result(
            "海克斯大乱斗查询：\n"
            "/海斗 —— 英雄胜率榜\n"
            "/海斗 阿狸 —— 单英雄胜率+海克斯适配榜\n"
            "/海斗海克斯 —— 海克斯强度榜（全部）\n"
            "/海斗海克斯 棱彩 —— 只看棱彩强化\n"
            "/海斗海克斯 泰坦 —— 查单个海克斯的强度与适配英雄"
        )

    @filter.command("海斗海克斯")
    async def haidou_augs(self, event: AstrMessageEvent, query: str = ""):
        """海克斯榜：带等级（棱彩/黄金/白银）筛选，或带强化名查详情"""
        try:
            yield event.chain_result(
                [Image.fromFileSystem(str(await self._augment_view(query)))]
            )
        except Exception as e:
            logger.error(f"haidou augs: {traceback_exc()}")
            yield event.plain_result(f"海斗数据查询失败了：{e}")

    # ---------------- llm tool ----------------

    @filter.llm_tool(name="haidou_query")
    async def haidou_query(
        self, event: AstrMessageEvent, action: str, keyword: str = ""
    ) -> str:
        """查询英雄联盟海克斯大乱斗（海斗/极地大乱斗强化）数据。action 取值: "rank"=英雄胜率榜; "hero"=按英雄名查胜率与推荐海克斯; "augment"=按海克斯名查强度与适配英雄。
        Args:
            action: 查询类型，可选 rank / hero / augment
            keyword: 英雄名或海克斯名；action 为 rank 时留空
        """
        try:
            if action == "rank":
                ds, heroes = await self._get_hero_rank()
                meta = await self._get_hero_meta()
                lines = [f"海斗英雄胜率榜（{self._fmt_date(ds)}，前15）："]
                for h in sorted(heroes, key=lambda x: x["rank"])[:15]:
                    m = meta.get(h["champ_id"], {})
                    name = m.get("title") or m.get("name") or h["champ_id"]
                    lines.append(
                        f"{h['rank']}. {name} 胜率{h['win'] * 100:.1f}% 选取{h['pick'] * 100:.1f}%"
                    )
                return "\n".join(lines)
            if action == "hero":
                hero = await self._find_hero(keyword)
                if not hero:
                    return f"没找到英雄「{keyword}」"
                champ_id = int(hero["heroId"])
                _, heroes = await self._get_hero_rank()
                mine = next((h for h in heroes if h["champ_id"] == champ_id), None)
                detail = await self._get_hero_detail(champ_id)
                aug_meta = await self._get_augment_meta()
                lines = [f"{hero.get('title', '')}{hero.get('name', '')}（海斗）"]
                if mine:
                    lines.append(
                        f"官方排名 {mine['rank']}，胜率 {mine['win'] * 100:.1f}%，选取率 {mine['pick'] * 100:.1f}%"
                    )
                else:
                    lines.append("当前不在胜率统计中")
                augs = detail["augs"][:8]
                if augs:
                    lines.append("推荐海克斯（按选取率）：")
                    for a in augs:
                        m = aug_meta.get(a["aug_id"], {})
                        lines.append(
                            f"- {m.get('name_cn', a['aug_id'])}（{LEVEL_CN.get(m.get('level', ''), '')}）"
                            f" 选取{a['pick'] * 100:.1f}% 评级{a['grade']}"
                        )
                return "\n".join(lines)
            if action == "augment":
                hit = await self._find_augment(keyword)
                if not hit:
                    return f"没找到海克斯「{keyword}」"
                stat, meta = hit
                _, augs = await self._get_rune_rank()
                hero_meta = await self._get_hero_meta()
                rank = (
                    sorted((a["win"] for a in augs), reverse=True).index(stat["win"])
                    + 1
                )
                avg = sum(a["win"] for a in augs) / max(1, len(augs))
                best = "、".join(
                    (
                        hero_meta.get(cid, {}).get("title")
                        or hero_meta.get(cid, {}).get("name")
                        or "?"
                    )
                    for cid in stat["best_heroes"]
                )
                level = LEVEL_CN.get(meta.get("level", ""), meta.get("level", ""))
                return (
                    f"{meta.get('name_cn', keyword)}（{level}）："
                    f"胜率 {stat['win'] * 100:.1f}%（全体第 {rank}/{len(augs)}，平均 {avg * 100:.1f}%），"
                    f"选取率 {stat['pick'] * 100:.1f}%，最适配英雄：{best}"
                )
            return "action 只支持 rank / hero / augment"
        except Exception as e:
            logger.error(f"haidou llm_tool: {traceback_exc()}")
            return f"查询失败: {e}"

    # ---------------- cards ----------------

    async def _augment_view(self, query: str = "") -> Path:
        """Dispatch: empty/level -> filtered list, otherwise augment detail."""
        for cn in LEVEL_CN.values():
            if query and query == cn:
                return await self._augment_card(query)
        if not query:
            return await self._augment_card("")
        hit = await self._find_augment(query)
        if not hit:
            raise RuntimeError(
                f"没找到海克斯「{query}」，试试完整名字（如：泰坦的坚决）或等级（棱彩/黄金/白银）"
            )
        return await self._augment_detail_card(*hit)

    async def _rank_card(self) -> Path:
        ds, heroes = await self._get_hero_rank()
        meta = await self._get_hero_meta()
        top_n = int(self.config.get("rank_top_n", 60))
        heroes_sorted = sorted(heroes, key=lambda h: h["rank"])
        return render_rank_card(
            heroes_sorted[:top_n],
            meta,
            self._fmt_date(ds),
            self.data_dir / "cards" / "rank.png",
        )

    async def _augment_card(self, level: str = "") -> Path:
        aug_date, augs = await self._get_rune_rank()
        aug_meta = await self._get_augment_meta()
        hero_meta = await self._get_hero_meta()
        level_key = ""
        for k, v in LEVEL_CN.items():
            if level and (level == v or level.lower() == k.lower()):
                level_key = k
                break
        if level and not level_key:
            raise RuntimeError("等级只支持：棱彩 / 黄金 / 白银")
        rows = []
        top_n = int(self.config.get("augment_top_n", 30))
        for a in augs:
            meta = aug_meta.get(a["aug_id"], {})
            if level_key and meta.get("level") != level_key:
                continue
            rows.append((a, meta))
            if len(rows) >= top_n:
                break
        icons = [await self._download_icon(m.get("large_Icon", "")) for _, m in rows]
        title = f"海克斯强度榜{'·' + LEVEL_CN[level_key] if level_key else ''}"
        return render_augment_card(
            rows,
            icons,
            hero_meta,
            self._fmt_date(aug_date),
            title,
            self.data_dir / "cards" / "augs.png",
        )

    async def _augment_detail_card(self, stat: dict, meta: dict) -> Path:
        aug_date, augs = await self._get_rune_rank()
        hero_meta = await self._get_hero_meta()
        _, heroes = await self._get_hero_rank()
        hero_wins = {h["champ_id"]: h["win"] for h in heroes}
        avg_win = sum(a["win"] for a in augs) / max(1, len(augs))
        win_rank = sorted((a["win"] for a in augs), reverse=True).index(stat["win"]) + 1
        best = []
        for cid in stat["best_heroes"]:
            m = hero_meta.get(cid, {})
            best.append((m, hero_wins.get(cid)))
        aug_icon = await self._download_icon(meta.get("large_Icon", ""))
        hero_icons = [
            await self._download_icon(
                f"https://game.gtimg.cn/images/lol/act/img/champion/{m.get('alias', '')}.png"
            )
            for m, _ in best
        ]
        return render_augment_detail(
            stat,
            meta,
            best,
            avg_win,
            win_rank,
            len(augs),
            hero_meta,
            self._fmt_date(aug_date),
            [aug_icon, *hero_icons],
            self.data_dir / "cards" / f"aug_{stat['aug_id']}.png",
        )

    async def _hero_card(self, hero: dict) -> Path:
        champ_id = int(hero["heroId"])
        ds, heroes = await self._get_hero_rank()
        mine = next((h for h in heroes if h["champ_id"] == champ_id), None)
        detail = await self._get_hero_detail(champ_id)
        aug_meta = await self._get_augment_meta()
        hero_meta = await self._get_hero_meta()
        portrait = await self._download_icon(
            f"https://game.gtimg.cn/images/lol/act/img/champion/{hero.get('alias', '')}.png"
        )
        aug_rows = detail["augs"][: int(self.config.get("hero_aug_top_n", 10))]
        aug_icons = [
            await self._download_icon(
                aug_meta.get(a["aug_id"], {}).get("large_Icon", "")
            )
            for a in aug_rows
        ]
        return render_hero_card(
            hero,
            mine,
            detail,
            aug_rows,
            aug_icons,
            aug_meta,
            hero_meta,
            portrait,
            self._fmt_date(ds),
            self.data_dir / "cards" / f"hero_{champ_id}.png",
        )


def traceback_exc() -> str:
    import traceback

    return traceback.format_exc()
