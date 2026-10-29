"""download stage — 抖音无水印下载。

三步:① 输入取 modal_id(分享文本抽短链→302 跳转 / 短链 / modal_id 直链均可);
② playwright 开真实浏览器(带登录态)打开视频页,拦截 aweme detail API 取播放地址
   (纯 requests 会被反爬 JSVM 壳拦死,2026-08 起 RENDER_DATA 服务端渲染已下线);
③ requests 流式下载(Referer+UA,不需要 cookie)。
"""

from __future__ import annotations

import asyncio
import json
import re
import time
from pathlib import Path

import requests
from loguru import logger

from ..pipeline import Param, Port, Stage, StageContext

UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)
DETAIL_API = "aweme/v1/web/aweme/detail"
DETAIL_WAIT_TIMEOUT = 45  # 浏览器内等详情 API 响应的上限(秒)


class DouyinDownloadError(RuntimeError):
    """抖音下载失败(链接无效/登录态失效/风控),message 带排障指引。"""


def get_modal_id(share_input: str) -> str:
    """从分享文本/短链/直链中提取 modal_id(视频 ID)。无需 cookie。"""
    share_input = share_input.strip()
    # 形态 1:modal_id 直链(www.douyin.com/...?modal_id=xxx 或 /video/xxx)
    m = re.search(r"modal_id=(\d+)", share_input) or re.search(r"douyin\.com/video/(\d+)", share_input)
    if m:
        return m.group(1)
    # 形态 2:分享文本或短链,跟随 302 取最终 URL
    m = re.search(r"https://v\.douyin\.com/[\w\-]+/?", share_input)
    if not m:
        raise DouyinDownloadError(f"无法从输入中识别抖音链接: {share_input[:80]}")
    try:
        r = requests.get(m.group(), headers={"user-agent": UA}, allow_redirects=True, timeout=20)
        r.raise_for_status()
    except requests.RequestException as e:
        raise DouyinDownloadError(f"短链跳转失败(检查网络): {e}") from e
    m2 = re.search(r"douyin\.com/video/(\d+)", r.url) or re.search(r"modal_id=(\d+)", r.url)
    if not m2:
        raise DouyinDownloadError(f"短链跳转后未识别视频 ID,最终 URL: {r.url}")
    return m2.group(1)


def _storage_state_from_cookie(cookie: str) -> dict:
    """把 "k=v; k=v" cookie 串转成 playwright storage_state(供显式传 cookie 的老用法)。"""
    cookies = []
    for pair in cookie.split(";"):
        pair = pair.strip()
        if not pair or "=" not in pair:
            continue
        k, v = pair.split("=", 1)
        cookies.append({"name": k, "value": v, "domain": ".douyin.com", "path": "/"})
    return {"cookies": cookies, "origins": []}


async def _fetch_play_info_async(modal_id: str, storage_state) -> dict:
    """开浏览器(带登录态)打开视频页,拦截 detail API,返回 {play_url, title}。"""
    from playwright.async_api import async_playwright

    from ..publishers.douyin import CHROME_ARGS, STEALTH_JS, _find_chrome_executable

    details: list[dict] = []
    async with async_playwright() as pw:
        launch_kwargs: dict = {"headless": True, "args": CHROME_ARGS}
        executable = _find_chrome_executable()
        if executable:
            launch_kwargs["executable_path"] = executable
        browser = await pw.chromium.launch(**launch_kwargs)
        try:
            context = await browser.new_context(
                storage_state=storage_state,
                viewport={"width": 1280, "height": 800},
                locale="zh-CN",
                timezone_id="Asia/Shanghai",
            )
            await context.add_init_script(STEALTH_JS)
            page = await context.new_page()

            async def on_response(resp) -> None:
                if DETAIL_API in resp.url:
                    try:
                        body = await resp.json()
                    except Exception:
                        return
                    if isinstance(body, dict) and body.get("aweme_detail"):
                        details.append(body)

            page.on("response", on_response)
            await page.goto(f"https://www.douyin.com/video/{modal_id}", timeout=60_000, wait_until="domcontentloaded")
            deadline = time.time() + DETAIL_WAIT_TIMEOUT
            while not details and time.time() < deadline:
                await asyncio.sleep(1)
        finally:
            await browser.close()

    if not details:
        raise DouyinDownloadError(
            "浏览器未抓到视频详情——登录态可能失效(重跑 vh login douyin)、链接无效或被风控,请稍后重试"
        )
    detail = details[0]["aweme_detail"]
    video = detail.get("video") or {}
    # 优先默认播放流,其次码率列表;url_list[0] 即 CDN 地址
    candidates = [video.get("play_addr")]
    candidates += [b.get("play_addr") for b in (video.get("bit_rate") or [])]
    play_url = ""
    for c in candidates:
        urls = (c or {}).get("url_list") or []
        if urls:
            play_url = urls[0]
            break
    if not play_url:
        raise DouyinDownloadError("详情接口结构与预期不符(抖音可能改版):video 下无可用 play_addr")
    if not play_url.startswith("https://"):
        play_url = "https://" + play_url
    return {"play_url": play_url, "title": detail.get("desc", "")}


def get_play_info(modal_id: str, cookie: str = "", storage_state_path: Path | None = None) -> dict:
    """抓播放地址,返回 {play_url, title, modal_id}。登录态优先用 storage_state 文件。"""
    if storage_state_path and storage_state_path.exists():
        state: str | dict = str(storage_state_path)
    elif cookie:
        state = _storage_state_from_cookie(cookie)
    else:
        raise DouyinDownloadError(
            "抓播放地址需要抖音登录态:先运行 vh login douyin,或用 params.douyin_cookie / VH_DOUYIN_COOKIE 显式给 cookie"
        )
    try:
        info = asyncio.run(_fetch_play_info_async(modal_id, state))
    except DouyinDownloadError:
        raise
    except Exception as e:
        raise DouyinDownloadError(f"浏览器抓取视频详情失败: {e}") from e
    info["modal_id"] = modal_id
    return info


def download_file(url: str, out_path: Path) -> Path:
    headers = {"Referer": "https://www.douyin.com/", "User-Agent": UA}
    with requests.get(url, headers=headers, stream=True, timeout=60) as r:
        r.raise_for_status()
        out_path.parent.mkdir(parents=True, exist_ok=True)
        with open(out_path, "wb") as f:
            for chunk in r.iter_content(chunk_size=1 << 16):
                if chunk:
                    f.write(chunk)
    if out_path.stat().st_size == 0:
        out_path.unlink()
        raise DouyinDownloadError("下载到空文件,播放地址可能已过期")
    return out_path


class DownloadStage(Stage):
    name = "download"
    title = "抖音下载"
    category = "输入"
    description = "按分享链接下载抖音视频(需 vh login douyin 登录态)"
    outputs = {"video": Port("video", "视频"), "meta": Port("json", "元信息")}
    params = (Param("share_url", "抖音分享链接", "string"),)

    def enabled(self, ctx: StageContext) -> bool:
        return bool(ctx.params.get("share_url"))

    def run(self, ctx: StageContext) -> dict[str, Path]:
        share_url = ctx.params["share_url"]
        cookie = ctx.params.get("douyin_cookie") or ctx.settings.douyin_cookie
        login_state = ctx.settings.abs_path(ctx.settings.publish_cookies_dir) / "douyin.json"
        if not cookie and login_state.exists():
            logger.info(f"未显式给 cookie,使用 vh login 保存的登录态: {login_state}")

        modal_id = get_modal_id(share_url)
        logger.info(f"视频 ID: {modal_id}")
        info = get_play_info(modal_id, cookie=cookie, storage_state_path=login_state)
        logger.info(f"播放地址已获取,标题: {info['title'][:30]}")

        video = download_file(info["play_url"], ctx.stage_dir / "source.mp4")
        meta = ctx.stage_dir / "meta.json"
        meta.write_text(
            json.dumps(
                {"title": info["title"], "modal_id": modal_id, "share_url": share_url},
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        logger.info(f"下载完成: {video} ({video.stat().st_size / 1e6:.1f} MB)")
        return {"video": video, "meta": meta}
