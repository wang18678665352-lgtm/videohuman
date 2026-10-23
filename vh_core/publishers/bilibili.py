"""B站发布器 — biliup 纯 HTTP 上传(biliup.plugins.bili_webup)。

移植自 reuse/direct/publish/uploader/bilibili_uploader/main.py:
cookie 为 biliup 登录 JSON(cookie_info.cookies + token_info.access_token)。
库本身是同步的,直接同步封装。
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from loguru import logger

from .base import normalize_tags, truncate_title

# 投稿分区(平台改版需维护点):138 = 生活·搞笑,口播类视频常用;如需调整在 upload 传 tid
DEFAULT_TID = 138
# biliup 登录态 JSON 中必须存在的 cookie 键
REQUIRED_COOKIE_KEYS = ["SESSDATA", "bili_jct", "DedeUserID"]


class BilibiliPublisher:
    platform = "bilibili"

    def validate_cookies(self, cookies_path: Path) -> None:
        """预检 biliup 登录 JSON:文件存在、可解析、cookie_info/token_info 结构完整。"""
        if not cookies_path.exists():
            raise FileNotFoundError(
                f"未找到 B站登录态文件: {cookies_path}。"
                "请先用 biliup 登录导出 cookie JSON(参照 configs/cookies/README.md)"
            )
        try:
            data = json.loads(cookies_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as e:
            raise ValueError(f"B站 cookie 文件不是有效 JSON: {cookies_path} ({e})") from e
        cookies = data.get("cookie_info", {}).get("cookies")
        if not isinstance(cookies, list) or not cookies:
            raise ValueError(
                f"B站 cookie 文件缺少 cookie_info.cookies 列表: {cookies_path},应为 biliup 登录导出的 JSON"
            )
        names = {c.get("name") for c in cookies if isinstance(c, dict)}
        missing = [k for k in REQUIRED_COOKIE_KEYS if k not in names]
        if missing:
            raise ValueError(f"B站 cookie 缺少关键字段 {missing}: {cookies_path},登录态不完整,请重新导出")
        if not data.get("token_info", {}).get("access_token"):
            logger.warning(f"B站 cookie 缺少 token_info.access_token: {cookies_path},部分接口(如定时发布)可能失败")

    def extract_keys(self, cookies_path: Path) -> dict:
        """从 biliup 登录 JSON 提取 login_by_cookies 所需键值。"""
        data = json.loads(cookies_path.read_text(encoding="utf-8"))
        keys = {}
        for cookie in data["cookie_info"]["cookies"]:
            if cookie.get("name") in [*REQUIRED_COOKIE_KEYS, "DedeUserID__ckMd5"]:
                keys[cookie["name"]] = cookie["value"]
        token = data.get("token_info", {}).get("access_token")
        if token:
            keys["access_token"] = token
        return keys

    def _build_data(self, title: str, tags: list[str], publish_date: datetime | None):
        """装配 biliup Data(网络请求前的纯参数组装,可独立自检)。"""
        from biliup.plugins.bili_webup import Data

        data = Data()
        data.copyright = 1  # 自制
        data.title = title
        data.desc = title
        data.tid = DEFAULT_TID
        data.set_tag(tags)
        if publish_date:
            # 定时发布:B站要求距当前 ≥4 小时,过近由平台侧报错
            data.dtime = int(publish_date.timestamp())
        return data

    def upload(
        self,
        video: Path,
        title: str,
        tags: list[str],
        cover: Path | None,
        cookies_path: Path,
        publish_date: datetime | None = None,
    ) -> dict:
        from biliup.plugins.bili_webup import BiliBili

        self.validate_cookies(cookies_path)
        if not video.exists():
            raise FileNotFoundError(f"待上传视频不存在: {video}")
        title = truncate_title(self.platform, title)
        tags = normalize_tags(self.platform, tags)
        cookie_data = self.extract_keys(cookies_path)
        data = self._build_data(title, tags, publish_date)

        try:
            with BiliBili(data) as bili:
                bili.login_by_cookies(cookie_data)
                bili.access_token = cookie_data.get("access_token")
                # 上传视频文件,AUTO 线路自动选择,3 线程
                video_part = bili.upload_file(str(video), lines="AUTO", tasks=3)
                video_part["title"] = title
                data.append(video_part)
                if cover and cover.exists():
                    try:
                        data.cover = bili.cover_up(str(cover))
                    except Exception as e:
                        logger.warning(f"B站封面上传失败,继续使用默认封面: {e}")
                ret = bili.submit()  # 提交投稿
        except Exception as e:
            logger.exception("B站上传过程出错")
            return {"platform": self.platform, "ok": False, "detail": f"上传异常: {e}"}

        if ret.get("code") == 0:
            logger.info(f"B站投稿成功: {video.name} (bvid={ret.get('data', {}).get('bvid')})")
            return {"platform": self.platform, "ok": True, "detail": f"投稿成功 bvid={ret.get('data', {}).get('bvid')}"}
        msg = ret.get("message", "未知错误")
        logger.error(f"B站投稿失败: code={ret.get('code')} message={msg}")
        return {"platform": self.platform, "ok": False, "detail": f"投稿被拒: code={ret.get('code')} {msg}"}
