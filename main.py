import asyncio
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
        # 赛事 API
        self.match_cookies = {
            "kq_auth_token": "6ca6091b-18ae-468e-ab63-a58959a571b6",
            "kq_auth_user_id": "1134526",
            "kq_auth_ready": "%221%22",
        }
        self.match_headers = {
            "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36",
            "accept": "application/json",
            "content-type": "application/x-www-form-urlencoded",
        }
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
        # 调用锁：防止 /kqw player 被重复触发
        self._player_lock = asyncio.Lock()
        # 分页状态：/查看 和 /下一页 共用
        self._page_uid: str = ""
        self._page_num: int = 1
        # 赛事查询会话：用于 /kqw match → /搜索 分步查询
        self._match_session: dict[str, dict] = {}

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

        # 调用锁：已被占用说明有同参数请求正在执行，直接跳过
        if self._player_lock.locked():
            return

        yield event.plain_result(f"🔍 正在搜索「{name}」…")

        async with self._player_lock:
            players = await self._search_player(name)
            if players is None:
                return
            # 缓存此次搜索结果
            self._last_search_results = players
            self._last_keyword = name

        # 锁已释放，构建并发送结果
        if not players:
            yield event.plain_result(f"未找到「{name}」的相关数据，请检查名称后重试。")
            return

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
        lines.append("📌 发送 /查看 <序号> 查看球员详细比赛记录")
        lines.append("═══════════════════════════════════")

        yield event.plain_result("\n".join(lines))

    # ─── /查看 命令 ────────────────────────────

    @kqw.command("match")
    async def match(self, event: AstrMessageEvent, keyword: str = ""):
        """查询赛事信息。启动后请使用 /搜索 分步输入。"""
        chat_id = event.get_session_id()
        self._match_session[chat_id] = {"step": "start"}
        yield event.plain_result(
            "\U0001f3d3 请使用 /搜索 <城市名> 提供查询地区，\n"
            "或回复「/搜索 全国」查询全国赛事。\n"
            "例如：/搜索 杭州"
        )

    @filter.command("\u641c\u7d22")
    async def search(self, event: AstrMessageEvent, keyword: str = ""):
        """分步查询赛事信息。由 /kqw match 启动后使用。"""
        from datetime import datetime, timezone, timedelta
        chat_id = event.get_session_id()
        session = self._match_session.get(chat_id, {})
        step = session.get("step", "")

        if not step:
            yield event.plain_result("请先使用 /kqw match 启动赛事查询。")
            return

        kw = keyword.strip()

        if step == "start":
            # 第一步：输入城市
            if not kw:
                yield event.plain_result("请输入城市名，例如：/搜索 杭州")
                return
            city = "unlimited" if kw in ("\u5168\u56fd","unlimited","all") else kw
            session["city"] = city
            session["step"] = "time"
            self._match_session[chat_id] = session
            yield event.plain_result(
                f"\U0001f4c5 城市「{city}」已收到。请选择时间范围：\n"
                "  /搜索 1  （\u672c\u6708）\n"
                "  /搜索 2  （\u4eca\u5e74）\n"
                "  /搜索 3  （\u8fd1\u4e09\u4e2a\u6708）"
            )
            return

        if step == "time":
            # 第二步：选择时间
            if kw not in ("1","2","3"):
                yield event.plain_result("无效选择。请回复：/搜索 1（\u672c\u6708） /搜索 2（\u4eca\u5e74） /搜索 3（\u8fd1\u4e09\u6708）")
                return
            city = session.get("city", "unlimited")
            del self._match_session[chat_id]  # clear session

            tz = timezone(timedelta(hours=8))
            now = datetime.now(tz)
            if kw == "1":
                sd = datetime(now.year, now.month, 1, tzinfo=tz)
                ed = datetime(now.year, now.month+1, 1, tzinfo=tz) if now.month<12 else datetime(now.year+1,1,1,tzinfo=tz)
                lb = "\u672c\u6708"
            elif kw == "2":
                sd = datetime(now.year, 1, 1, tzinfo=tz)
                ed = datetime(now.year+1, 1, 1, tzinfo=tz)
                lb = "\u4eca\u5e74"
            else:
                sd = datetime(now.year, now.month-2, 1, tzinfo=tz) if now.month>2 else datetime(now.year-1, 11, 1, tzinfo=tz)
                ed = datetime(now.year, now.month+1, 1, tzinfo=tz) if now.month<12 else datetime(now.year+1,1,1,tzinfo=tz)
                lb = "\u8fd1\u4e09\u4e2a\u6708"

            yield event.plain_result(f"\U0001f50d 正在查询「{city}」{lb}的赛事\u2026")
            try:
                matches = await self._search_matches(city, int(sd.timestamp()), int(ed.timestamp()))
                if not matches:
                    yield event.plain_result(f"未找到「{city}」{lb}的赛事。")
                    return
                lines = ["\u2550"*35, f"  \U0001f3d3 赛事查询 \u00b7 {city}", f"  {lb} \u00b7 共 {len(matches)} 场", "\u2500"*35]
                for i, m in enumerate(matches[:10], 1):
                    lines.append(f"\n  [{i}] {m.get('title','?')}")
                    lines.append(f"      时间: {m.get('starttime','?')}  |  {m.get('distance','')}")
                    if m.get("arena_name"):
                        lines.append(f"      场馆: {m['arena_name']}")
                if len(matches) > 10:
                    lines.append(f"\n  \u2026还有 {len(matches)-10} 场")
                lines.append("\u2550"*35)
                yield event.plain_result("\n".join(lines))
            except Exception as e:
                yield event.plain_result(f"查询出错: {str(e)}")

    @filter.command("\u67e5\u770b")
    async def view_detail(self, event: AstrMessageEvent, index_str: str = ""):
        """查看球员详细比赛记录。用法：/\u67e5\u770b 1"""
        index_str = index_str.strip()
        if not index_str or not index_str.isdigit():
            yield event.plain_result("请提供序号，例如：/\u67e5\u770b 1")
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
        nick = player.get("username2", "\u672a\u77e5")
        yield event.plain_result(f"\U0001f50d 正在查询「{nick}」的详细记录\u2026")
        from astrbot.api.message_components import Image, Plain
        try:
            records = await self._get_player_games(uid, page=1)
            self._page_uid = uid
            self._page_num = 1
            profile = await self._get_player_adv_profile(uid)
            if records is None:
                yield event.plain_result(f"未查询到「{nick}」的详细记录。")
                return
            lines = ["\u2550"*35, f"  \U0001f4cb {nick} \u00b7 \u73b2\u5458\u6863\u6848", f"  UID: {uid}", "\u2500"*35]
            if profile:
                rn = profile.get("realname", "")
                sex = "\u7537" if profile.get("sex") == "\u7537" else ("\u5973" if profile.get("sex") == "\u5973" else "\u672a\u77e5")
                age = profile.get("age", "?")
                loc = profile.get("resideprovince", "\u672a\u77e5")
                score = profile.get("score", "?")
                rank = profile.get("rank", "?")
                win = profile.get("win", "0")
                lose = profile.get("lose", "0")
                total = profile.get("total", "0")
                brand = profile.get("brand", "")
                top3 = profile.get("Top3OfBeatUsernameScore", [])
                desc = profile.get("description", "")
                lines.append(f"  \u59d3\u540d: {rn}")
                if sex != "\u672a\u77e5":
                    lines.append(f"  \u6027\u522b: {sex}  |  \u5e74\u9f84: {age}")
                lines.append(f"  \u5730\u533a: {loc}")
                lines.append(f"  \u79ef\u5206: {score}  |  \u6392\u540d: #{rank}")
                lines.append(f"  \u6218\u7ee9: {win}\u80dc {lose}\u8d1f (\u5171{total}\u76d8)  |  \u6700\u957f\u8fde\u80dc: 7\u573a")
                if brand:
                    lines.append(f"  \u5668\u6750: {brand}")
                lines.append("")
                lines.append("  \U0001f3c6 \u6218\u80dc\u8fc7\u7684\u6700\u9ad8\u5206\u5bf9\u624b:")
                for t in top3[:3]:
                    lines.append(f"    \u2022 {t}")
                lines.append("")
                lines.append(f"  \U0001f4dd {desc}")
                lines.append("")
            lines.append(f"  \U0001f4c5 \u6bd4\u8d5b\u8bb0\u5f55 (\u7b2c{self._page_num}\u9875, {len(records)}\u573a)")
            lines.append("\u2500"*35)
            show_records = records[:15]
            has_more = len(records) >= 15
            for i, r in enumerate(show_records, 1):
                opp = r.get("username2", "?")
                rd = r.get("result1", "")
                sc = r.get("score1", "0")
                dt = r.get("dateline", "?")
                win2 = "\u2705" if rd in ("2", "3") else "\u274c" if rd in ("0", "1") else "\u2795"
                lines.append(f"\n  [{i}] vs {opp}  {win2}")
                sc_text = f"\u79ef\u5206\u53d8\u52a8: {sc}" if sc and sc not in ("0", "") else "\u65e0\u79ef\u5206\u53d8\u52a8"
                lines.append("      \u7ed3\u679c: " + ("\u80dc" if win2=="\u2705" else "\u8d1f") + " | " + sc_text)
                lines.append(f"      \u65e5\u671f: {dt}")
            if len(records) > 15:
                lines.append(f"\n  \u2026\u8fd8\u6709 {len(records)-15} \u573a\u6bd4\u8d5b\u672a\u663e\u793a")
            if has_more:
                lines.append("")
                lines.append("\U0001f4cc \u53d1\u9001 /\u4e0b\u4e00\u9875 \u67e5\u770b\u66f4\u591a\u5bf9\u5c40")
            lines.append("\u2550"*35)
            text_result = "\n".join(lines)
            uid_str = str(uid).zfill(6)
            avatar_url = f"https://oss.kaiqiu.cc/avatar/000/{uid_str[:2]}/{uid_str[2:4]}/{uid_str[4:]}_avatar_big_1.jpg"
            yield event.chain_result([Image.fromURL(avatar_url), Plain(text_result)])
        except Exception as e:
            import traceback; logger.error(f"[KaiqiuPlugin] \u67e5\u770b\u5931\u8d25: {traceback.format_exc()}")
            yield event.plain_result(f"查询出错: {str(e)}")

    @filter.command("\u4e0b\u4e00\u9875")
    async def next_page(self, event: AstrMessageEvent):
        """查看下一页对局记录。"""
        if not self._page_uid:
            yield event.plain_result("请先使用 /\u67e5\u770b <\u5e8f\u53f7> \u67e5\u8be2\u7403\u5458\u4fe1\u606f\u540e\u518d\u7ffb\u9875\u3002")
            return
        next_p = self._page_num + 1
        yield event.plain_result(f"\U0001f4c4 \u52a0\u8f7d\u7b2c {next_p} \u9875\u2026")
        try:
            records = await self._get_player_games(self._page_uid, page=next_p)
            if not records:
                yield event.plain_result("\u6ca1\u6709\u66f4\u591a\u5bf9\u5c40\u8bb0\u5f55\u3002")
                return
            self._page_num = next_p
            lines = ["\u2550"*35, f"  \U0001f4cb \u6bd4\u8d5b\u8bb0\u5f55 (\u7b2c{self._page_num}\u9875)", "\u2500"*35]
            for i, r in enumerate(records[:15], 1):
                opp = r.get("username2", r.get("username1", "?"))
                rd = r.get("result1", "")
                sc = r.get("score1", "0")
                dt = r.get("dateline", "?")
                icon = "\u2705" if rd in ("2", "3") else "\u274c" if rd in ("0", "1") else "\u2795"
                lines.append(f"  [{i}] vs {opp}  {icon}")
                sc_txt = f"\u79ef\u5206\u53d8\u52a8: {sc}" if sc not in ("0", "") else "\u65e0\u79ef\u5206\u53d8\u52a8"
                lines.append(f"      \u7ed3\u679c: {'\u80dc' if icon=='\u2705' else '\u8d1f'} | {sc_txt}")
                lines.append(f"      \u65e5\u671f: {dt}")
            if len(records) >= 15:
                lines.append("")
                lines.append("\U0001f4cc \u53d1\u9001 /\u4e0b\u4e00\u9875 \u7ee7\u7eed\u67e5\u770b")
            lines.append("\u2550"*35)
            yield event.plain_result("\n".join(lines))
        except Exception as e:
            yield event.plain_result(f"翻页出错: {str(e)}")


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

    async def _get_player_adv_profile(self, uid: str) -> dict | None:
        """调用开球网 API 获取球员高级档案"""
        url = f"https://www.zeropo.xyz/api/user/adv_profile?uid={uid}"
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
        return body.get("data")

    async def _get_player_games(self, uid: str, page: int = 1) -> list[dict] | None:
        """调用开球网 API 获取球员对局记录（带分页）"""
        url = f"https://www.zeropo.xyz/api/user/getGames?uid={uid}&page={page}"
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
        records = body.get("data", {}).get("data", [])
        return records if records else None

    async def _search_matches(self, city: str, start_ts: int, end_ts: int, page: int = 1) -> list[dict] | None:
        """调用开球网 API 查询赛事"""
        city_param = "unlimitedCity" if city in ("全国", "unlimited", "all") else city
        # API 要求城市名带"市"后缀
        if city_param not in ("unlimited", "") and not city_param.endswith("市"):
            city_param += "市"
        data_dict = {
            "city": city_param,
            "eventTitle": "",
            "startMatchTimestamp": str(start_ts),
            "endMatchTimestamp": str(end_ts),
            "distance": "gt0",
            "search": "1",
            "searchResultSortType": "0",
            "lat": "30.184",
            "lng": "120.204",
            "page": str(page),
        }
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(
                "https://www.zeropo.xyz/api/match/lists",
                headers=self.match_headers,
                cookies=self.match_cookies,
                data=data_dict,
            )
            resp.raise_for_status()
            body = resp.json()
        if body.get("code") != 1:
            return None
        matches = body.get("data", {}).get("data", [])
        return matches if matches else None
