"""publish stage — 多平台发布(B站/抖音)。

输入:postprocess/final.mp4(必选)、postprocess/cover.png(可选封面)。
参数:publish_platforms(平台列表,空则本 stage 跳过)、publish_title(缺省取
rewrite/text.txt 首句)、publish_tags(逗号分隔字符串或列表)、publish_date(可选,
ISO 字符串或 datetime,定时发布)。
单平台失败不中断其他平台,逐平台结果写 publish/result.json,全部失败才抛异常。
"""

from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any

from loguru import logger

from ..pipeline import Stage, StageContext
from ..publishers import BilibiliPublisher, DouyinPublisher, PublisherBase

PUBLISHERS: dict[str, type] = {
    "bilibili": BilibiliPublisher,
    "douyin": DouyinPublisher,
}


def _default_title(ctx: StageContext) -> str:
    """缺省标题:rewrite/text.txt 首句(按标点/换行切),兜底 job_id。"""
    text_file = ctx.job_dir / "rewrite" / "text.txt"
    if text_file.exists():
        text = text_file.read_text(encoding="utf-8").strip()
        # 按中英文标点切首句:\w 匹配中日韩文字与字母数字,遇首个标点/换行即断
        first = re.split(r"[^\w\s]", text, maxsplit=1)[0].strip()
        if first:
            return first
    return ctx.job_id


def _parse_tags(raw: Any) -> list[str]:
    """publish_tags 支持逗号分隔字符串或列表。"""
    if not raw:
        return []
    if isinstance(raw, str):
        return [t.strip() for t in raw.replace("，", ",").split(",") if t.strip()]
    if isinstance(raw, (list, tuple)):
        return [str(t).strip() for t in raw if str(t).strip()]
    raise ValueError(f"params.publish_tags 类型不支持: {type(raw).__name__}(应为字符串或列表)")


def _parse_publish_date(raw: Any) -> datetime | None:
    """解析定时发布时间,统一返回**带时区**的 datetime(无时区按本机时区解释)。

    裸时间(如 "2026-08-08T20:00:00")若当 UTC 处理,抖音日期输入框会被填进早 8
    小时的时刻、B站 dtime 也会偏,所以这里一律本地化。
    """
    if not raw:
        return None
    if isinstance(raw, datetime):
        dt = raw
    elif isinstance(raw, str):
        try:
            dt = datetime.fromisoformat(raw.strip())
        except ValueError as e:
            raise ValueError(f"params.publish_date 不是合法 ISO 时间: {raw!r}(示例 2026-08-08T20:00:00)") from e
    else:
        raise ValueError(f"params.publish_date 类型不支持: {type(raw).__name__}")
    return dt if dt.tzinfo else dt.astimezone()


class PublishStage(Stage):
    name = "publish"

    def enabled(self, ctx: StageContext) -> bool:
        return bool(ctx.params.get("publish_platforms"))

    def run(self, ctx: StageContext) -> dict[str, Path]:
        video = ctx.job_dir / "postprocess" / "final.mp4"
        if not video.exists():
            raise FileNotFoundError(f"publish 需要 postprocess 产物 final.mp4(未找到: {video}),请先跑 postprocess")
        cover = ctx.job_dir / "postprocess" / "cover.png"
        cover = cover if cover.exists() else None

        title = (ctx.params.get("publish_title") or "").strip() or _default_title(ctx)
        tags = _parse_tags(ctx.params.get("publish_tags"))
        publish_date = _parse_publish_date(ctx.params.get("publish_date"))

        platforms = ctx.params["publish_platforms"]
        if isinstance(platforms, str):
            platforms = [p.strip() for p in platforms.split(",") if p.strip()]

        results: list[dict] = []
        for platform in platforms:
            cls = PUBLISHERS.get(platform)
            if cls is None:
                logger.error(f"未知发布平台: {platform}(可选: {list(PUBLISHERS)})")
                results.append({"platform": platform, "ok": False, "detail": f"未知平台,可选: {list(PUBLISHERS)}"})
                continue
            publisher: PublisherBase = cls()
            cookies_path = ctx.settings.publish_cookies / f"{platform}.json"
            logger.info(f"开始发布到 {platform}: 标题={title!r} 标签={tags}")
            try:
                result = publisher.upload(video, title, tags, cover, cookies_path, publish_date)
            except Exception as e:
                logger.exception(f"[{platform}] 发布失败,继续其他平台")
                result = {"platform": platform, "ok": False, "detail": str(e)}
            results.append(result)
            logger.info(f"[{platform}] 发布结果: ok={result['ok']} {result['detail']}")

        out = ctx.stage_dir / "result.json"
        out.write_text(
            json.dumps(
                {
                    "video": ctx.rel(video),
                    "title": title,
                    "tags": tags,
                    "results": results,
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )

        if not any(r["ok"] for r in results):
            raise RuntimeError("所有平台发布均失败: " + "; ".join(f"[{r['platform']}] {r['detail']}" for r in results))
        return {"result": out}
