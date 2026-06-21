import traceback

import httpx

from astrbot.api import logger
from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api.star import Context, Star, register


@register(
    "astrbot_plugin_kaiqiu",
    "Finch0714",
    "查询开球网球员与赛事信息。支持 /kqw player 查询，/查看 序号 查看详情。",
    "v1.1",
    "https://github.com/Finch0714/astrbot_plugin_kaiqiu",
)
class KaiqiuPlugin(Star):
    def __init__(self, context: Context):
        super().__init__(context)
        # 搜索 API
        self.search_url = "https://www.zeropo.xyz/api/user/lists"
        self.search_headers = {
            "User-Agent": "Mozilla/5.0 (Linux; Android 10; K) AppleWebKit/537.36",
            "accept": "application/json",
            "content-type": "application/x-www-form-urlencoded",
        }
        self.search_cookies = {"kq_auth_ready": "1"}

        # 详情 API
        self.detail_url_tpl = "https://www.zeropo.xyz/api/user/getUserScores?uid={}"
        self.detail_headers = {
            "User-Agent": "Mozilla/5.0 (Linux; Android 10; K) AppleWebKit/537.36",
            "accept": "application/json",
            "Referer": "https://www.zeropo.xyz/scores/3201",
        }
        self.detail_cookies = {
            "kq_auth_token": "6ca6091b-18ae-468e-ab63-a58959a571b6",
            "kq_auth_user_id": "1134526",
            "kq_auth_ready": "1",
        }

        # 临时缓存：最近一次搜索的球员列表，格式：[{uid, username2, realname, score, ...}]
        self._last_search_results: list[dict] = []
        self._last_keyword: str = ""

        # 调用锁：防止 AstrBot 框架重复触发同一命令
        self._call_lock: dict[str, float] = {}

    def _check_lock(self, key: str) -> bool:
        """检查锁。同一 key 在 1 秒内重复调用返回 True（拦截），否则返回 False（放行）。"""
        import time
        now = time.time()
        last = self._call_lock.get(key, 0)
        if now - last < 1.0:
            return True  # 重复调用，拦截
        self._call_lock[key] = now
        return False

    async def initialize(self):
        logger.info("[KaiqiuPlugin] 开球网数据查询插件已加载")

    async def terminate(self):
        pass

    # ─── 指令组 ─────────────────────────────────

    @filter.command_group("kqw")
    def kqw(self):
        """开球网数据查询"""
        pass

    @kqw.command("player")
    async def player(self, event: AstrMessageEvent, name: str = ""):
        """查询球员信息。用法：/kqw player 马龙"""
        name = name.strip()
        if not name:
            yield event.plain_result("请提供球员名称，例如：/kqw player 马龙")
            return

        # 调用锁
        lock_key = f"player:{name}"
        if self._check_lock(lock_key):
            return

        yield event.plain_result(f"🔍 正在搜索「{name}」…")

        try:
            players = await self._search_player(name)
            if players is None:
                yield event.plain_result(f"未找到「{name}」的相关数据，请检查名称后重试。")
                return

            # 缓存此次搜索结果
            self._last_search_results = players
            self._last_keyword = name

            # 构建结果文本
            lines = [
                "═══════════════════════════════════",
                f"  开球网 · 球员搜索结果「{name}」",
                f"  共找到 {len(players)} 条记录",
                "───────────────────────────────",
            ]

            for i, p in enumerate(players, 1):
                nick = p.get("username2", "未知")
                real = p.get("realname", "")
                score = p.get("score", "N/A")
                prov = p.get("resideprovince", "")
                city = p.get("residecity", "")
                birth = p.get("birthyear", "")
                location = f"{prov} {city}".strip() or "未知"
                age = f"{birth}年" if birth and birth != "0" else "未知"

                lines.append(f"\n  [{i}] {nick}")
                if real and real != nick:
                    lines.append(f"      姓名: {real}")
                lines.append(f"      积分: {score}  |  地区: {location}")
                if age != "未知":
                    lines.append(f"      出生: {age}")

            lines.append("")
            lines.append("───────────────────────────────")
            lines.append("📌 发送 /查看 数字 查看球员详细比赛记录")
            lines.append("═══════════════════════════════════")

            yield event.plain_result("\n".join(lines))

        except Exception as e:
            logger.error(f"[KaiqiuPlugin] 查询失败: {traceback.format_exc()}")
            yield event.plain_result(f"查询出错: {str(e)}")

    # ─── /查看 命令 ────────────────────────────

    @filter.command("查看")
    async def view_detail(self, event: AstrMessageEvent, index_str: str = ""):
        """查看球员详细比赛记录。用法：/查看 1"""
        index_str = index_str.strip()
        if not index_str or not index_str.isdigit():
            yield event.plain_result("请提供序号，例如：/查看 1")
            return

        idx = int(index_str) - 1
        if not self._last_search_results:
            yield event.plain_result("当前没有搜索结果缓存，请先使用 /kqw player 搜索")
            return

        if idx < 0 or idx >= len(self._last_search_results):
            yield event.plain_result(f"序号超出范围（1-{len(self._last_search_results)}），请重新输入。")
            return

        player = self._last_search_results[idx]
        uid = player.get("uid")
        nick = player.get("username2", "未知")

        yield event.plain_result(f"🔍 正在查询「{nick}」的详细记录…")

        try:
            records = await self._get_player_detail(uid)
            if records is None:
                yield event.plain_result(f"未查询到「{nick}」的详细记录。")
                return

            # 构建详情文本
            lines = [
                "═══════════════════════════════════",
                f"  📋 {nick} · 比赛记录",
                f"  UID: {uid}",
                f"  共 {len(records)} 场比赛",
                "───────────────────────────────",
            ]

            # 只显示最近 15 场，避免刷屏
            show_records = records[:15]
            for i, r in enumerate(show_records, 1):
                date = r.get("dateline", "") or "未知日期"
                # dateline 格式 YYYYMMDD → 格式化
                if len(date) == 8:
                    date = f"{date[:4]}-{date[4:6]}-{date[6:]}"
                title = r.get("title", "未知赛事")
                score = r.get("postscore", "N/A")
                loc = r.get("location", "")

                lines.append(f"\n  [{i}] {title}")
                lines.append(f"      积分: {score}  |  日期: {date}")
                if loc:
                    lines.append(f"      地点: {loc}")

            if len(records) > 15:
                lines.append(f"\n  …还有 {len(records) - 15} 场比赛未显示")

            lines.append("═══════════════════════════════════")
            yield event.plain_result("\n".join(lines))

        except Exception as e:
            logger.error(f"[KaiqiuPlugin] 查看详情失败: {traceback.format_exc()}")
            yield event.plain_result(f"查询出错: {str(e)}")

    # ─── 内部方法 ───────────────────────────────

    async def _search_player(self, keyword: str) -> list[dict] | None:
        """调用开球网 API 搜索球员"""
        data = f"page=1&key={keyword}&sort=2&index=0"
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(
                self.search_url,
                headers=self.search_headers,
                cookies=self.search_cookies,
                data=data,
            )
            resp.raise_for_status()
            body = resp.json()

        if body.get("code") != 1:
            return None
        players = body.get("data", {}).get("data", [])
        return players if players else None

    async def _get_player_detail(self, uid: str) -> list[dict] | None:
        """调用开球网 API 获取球员详细比赛记录"""
        url = self.detail_url_tpl.format(uid)
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(
                url,
                headers=self.detail_headers,
                cookies=self.detail_cookies,
            )
            resp.raise_for_status()
            body = resp.json()

        if body.get("code") != 1:
            return None
        records = body.get("data", [])
        return records if records else None
