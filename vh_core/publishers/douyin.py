"""抖音发布器 — playwright 驱动 creator.douyin.com 上传页。

移植自 reuse/direct/publish/uploader/douyin_uploader/main.py:
原代码依赖的 conf / utils.base_social_media / utils.log 等外部模块在此内联重写:
- 反检测 init script:STEALTH_JS(抄自原 _setup_page_permissions 注入的 JS)
- Chrome 可执行文件查找:_find_chrome_executable(原逻辑照搬)
- 页面选择器:集中在 DOUYIN_SELECTORS,平台改版需维护点
cookie 为 playwright storage_state JSON;async 实现 + asyncio.run 包装成同步接口。
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import random
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from loguru import logger

from .base import normalize_tags, truncate_title

UPLOAD_URL = "https://creator.douyin.com/creator-micro/content/upload"
MANAGE_URL = "https://creator.douyin.com/creator-micro/content/manage"

# ===== 平台改版需维护点:以下选择器随抖音创作者平台前端改版可能失效,实发时需校准 =====
SEL_LOADING = "div.semi-spin-children > div.semi-spin-item"  # 页面加载指示器
SEL_FILE_INPUT = "input[type='file']"  # 视频文件 input
SEL_PROGRESS_CONTAINER = "div.progress-div"  # 上传进度条容器
SEL_PROGRESS_TEXT = "div.progress-div"  # 进度文本(取 inner_text 尾部的 %)
SEL_REUPLOAD_INPUT = "div.progress-div [class^='upload-btn-input']"  # "重新上传"入口(=上传完成)
SEL_ERROR_MESSAGE = "[class*='error-message']"  # 上传出错提示
SEL_TITLE_INPUT = "input[placeholder*='填写作品标题']"  # 标题输入框
SEL_TAG_CONTAINER = ".zone-container"  # 标题/话题输入区(输 #话题)
SEL_SCHEDULE_RADIO = "[class^='radio']:has-text('定时发布')"  # 定时发布单选
SEL_SCHEDULE_INPUT = ".semi-input[placeholder='日期和时间']"  # 定时时间输入框

UPLOAD_TIMEOUT = 600  # 视频上传等待上限(秒)
PUBLISH_TIMEOUT = 30  # 点击发布后等待跳转上限(秒)
VERIFY_WAIT_TIMEOUT = 300  # 出现验证弹窗时等用户完成的上限(秒)
SCHEDULE_MIN_AHEAD = 2 * 3600  # 抖音定时发布要求距当前 ≥2 小时(平台限制)

# 北京时间(UTC+8,无夏令时):定时发布日期框按该时区的墙上时间填写
CST = timezone(timedelta(hours=8))

# 平台改版需维护点:发布时的安全验证弹窗(短信验证码/滑块等)
VERIFY_SELECTORS = [
    "[class*='uc-second-verify']",
    ".semi-modal:has-text('验证')",
    "div[role='dialog']:has-text('验证')",
]

# 反检测 init script(原 _setup_page_permissions 注入 JS 照搬):去 webdriver 痕迹、
# 伪装插件/语言/屏幕信息、覆盖权限查询、随机鼠标事件
STEALTH_JS = """
Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
Object.defineProperty(navigator, 'plugins', { get: () => [1, 2, 3, 4, 5] });
Object.defineProperty(navigator, 'languages', { get: () => ['zh-CN', 'zh', 'en'] });
const originalQuery = window.navigator.permissions.query;
window.navigator.permissions.query = (parameters) => (
    parameters.name === 'notifications' ?
        Promise.resolve({ state: Notification.permission }) :
        originalQuery(parameters)
);
Object.defineProperty(screen, 'availWidth', { get: () => 1920 });
Object.defineProperty(screen, 'availHeight', { get: () => 1040 });
"""

CHROME_ARGS = [
    "--disable-blink-features=AutomationControlled",
    "--start-maximized",
    "--disable-extensions",
    "--no-sandbox",
    "--disable-dev-shm-usage",
    "--no-first-run",
    "--disable-background-timer-throttling",
    "--disable-backgrounding-occluded-windows",
    "--disable-renderer-backgrounding",
]


def _find_chrome_executable() -> str | None:
    """搜索 Windows 平台 Chrome 常见安装位置(原逻辑照搬),找不到则用 playwright 内置 chromium。"""
    candidates = [
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        os.path.expanduser(r"~\AppData\Local\Google\Chrome\Application\chrome.exe"),
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    ]
    for p in candidates:
        if os.path.exists(p):
            logger.info(f"找到本地浏览器: {p}")
            return p
    logger.warning("未找到本地 Chrome/Edge,将使用 playwright 内置 chromium")
    return None


async def _simulate_human_behavior(page) -> None:
    """随机滚动 + 鼠标移动,降低风控概率。"""
    try:
        await page.evaluate(
            "() => { const s = Math.random() * 200 + 100; window.scrollBy(0, s);"
            " setTimeout(() => window.scrollBy(0, -s / 2), 500); }"
        )
        await asyncio.sleep(1 + random.random() * 2)
        await page.mouse.move(random.randint(100, 800), random.randint(100, 600))
    except Exception as e:
        logger.warning(f"模拟人类行为时出错(忽略): {e}")


async def _check_upload_complete(page) -> bool:
    """上传完成的两种判据:'重新上传'文本可见,或视频 input 重新出现。"""
    try:
        if await page.get_by_text("重新上传", exact=True).is_visible(timeout=3000):
            return True
        if await page.locator('input[accept*="video/mp4"]').is_visible(timeout=2000):
            return True
    except Exception:
        pass
    return False


async def _set_thumbnail(page, thumbnail_path: Path) -> None:
    """设置封面图:选择封面 → 弹窗内上传图片 → 完成。失败不阻断主流程。"""
    if not thumbnail_path or not thumbnail_path.exists():
        return
    try:
        # 平台改版需维护点:封面入口/弹窗/完成按钮选择器
        await page.click('button:has-text("选择封面")', timeout=5000)
        await page.wait_for_selector("div.semi-modal-content", timeout=5000)
        await asyncio.sleep(1)
        upload_input = page.locator("input[type='file'][accept*='image']")
        if await upload_input.count() == 0:
            upload_input = page.locator("input.semi-upload-hidden-input").first
        await upload_input.set_input_files(str(thumbnail_path))
        await asyncio.sleep(3)
        await page.click("button:has-text('完成')", timeout=5000)
        await asyncio.sleep(1)
        logger.info("封面设置完成")
    except Exception as e:
        logger.warning(f"设置封面失败,继续后续流程: {e}")
        with contextlib.suppress(Exception):
            await page.keyboard.press("Escape")


async def _set_schedule_time(page, publish_date: datetime) -> None:
    """设置定时发布(原 set_schedule_time_douyin 照搬)。

    日期框只认**北京时间**墙上时间:publish_date 带时区时先换到 UTC+8 再格式化,
    否则东八区用户填的 20:00 会被填成 12:00。
    """
    if publish_date.tzinfo is not None:
        publish_date = publish_date.astimezone(CST)
    await page.locator(SEL_SCHEDULE_RADIO).click()
    await asyncio.sleep(1)
    await page.locator(SEL_SCHEDULE_INPUT).click()
    await page.keyboard.press("Control+KeyA")
    await page.keyboard.type(publish_date.strftime("%Y-%m-%d %H:%M"))
    await page.keyboard.press("Enter")
    await asyncio.sleep(1)


async def _find_visible_verify(page):
    """返回第一个可见的验证弹窗 locator,没有则 None。"""
    for sel in VERIFY_SELECTORS:
        loc = page.locator(sel)
        try:
            if await loc.count() and await loc.first.is_visible():
                return loc.first
        except Exception:
            continue
    return None


async def _wait_publish_result(page, context, cookies_path: Path) -> bool:
    """点击发布后判定结果。

    关键:跳转管理页 ≠ 发布成功——平台可能先弹短信/滑块验证,验证不过发布不生效。
    所以先查验证弹窗(出现则保持浏览器打开,等用户完成),确认无弹窗后再看跳转。
    """
    deadline = time.time() + PUBLISH_TIMEOUT
    announced = False
    while time.time() < deadline:
        verify = await _find_visible_verify(page)
        if verify is not None:
            if not announced:
                announced = True
                logger.warning(
                    f"检测到发布安全验证(短信/滑块),请在浏览器中完成,浏览器会保持打开(最长 {VERIFY_WAIT_TIMEOUT} 秒)"
                )
            try:
                await verify.wait_for(state="hidden", timeout=VERIFY_WAIT_TIMEOUT * 1000)
            except Exception:
                logger.error("等待验证完成超时")
                return False
            logger.info("验证已完成,等待发布结果...")
            await asyncio.sleep(3)
            deadline = time.time() + PUBLISH_TIMEOUT  # 验证完成后重新计发布判定
            continue
        if MANAGE_URL in page.url:
            # 截图取证:判定瞬间的页面留档,便于人工复核(吃过假阳性的亏)
            shot = cookies_path.parent / f"publish_result_{int(time.time())}.png"
            try:
                await page.screenshot(path=str(shot))
                logger.info(f"跳转管理页且无验证弹窗,判定发布成功,截图留档: {shot}")
            except Exception:
                logger.info("跳转管理页且无验证弹窗,判定发布成功(截图失败)")
            await context.storage_state(path=str(cookies_path))
            return True
        await asyncio.sleep(2)
    shot = cookies_path.parent / f"publish_fail_{int(time.time())}.png"
    try:
        await page.screenshot(path=str(shot))
        logger.error(f"未检测到发布成功标识,当前 URL: {page.url},截图留档: {shot}")
    except Exception:
        logger.error(f"未检测到发布成功标识,当前 URL: {page.url}")
    return False


class DouyinPublisher:
    platform = "douyin"

    def validate_cookies(self, cookies_path: Path) -> None:
        """预检 playwright storage_state:文件存在、JSON dict、cookies 非空。"""
        if not cookies_path.exists():
            raise FileNotFoundError(
                f"未找到抖音登录态文件: {cookies_path}。请先运行 vh login douyin 并在弹出的浏览器中登录"
            )
        try:
            data = json.loads(cookies_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as e:
            raise ValueError(f"抖音 cookie 文件不是有效 JSON: {cookies_path} ({e})。请先运行 vh login douyin") from e
        if not isinstance(data, dict) or not data.get("cookies"):
            raise ValueError(
                f"抖音 cookie 文件缺少 storage_state 结构(cookies 为空): {cookies_path}。请先运行 vh login douyin"
            )

    async def _upload_async(
        self,
        video: Path,
        title: str,
        tags: list[str],
        cover: Path | None,
        cookies_path: Path,
        publish_date: datetime | None,
    ) -> bool:
        if publish_date is not None and (publish_date - datetime.now(CST)).total_seconds() < SCHEDULE_MIN_AHEAD:
            # 平台硬限制:提前量不足会被拒,直接给可排障的报错而不是在页面上空转
            raise ValueError(
                f"抖音定时发布需距当前至少 {SCHEDULE_MIN_AHEAD // 3600} 小时,"
                f"当前设定: {publish_date.astimezone(CST):%Y-%m-%d %H:%M}(北京时间)"
            )
        from playwright.async_api import async_playwright

        async with async_playwright() as pw:
            executable = _find_chrome_executable()
            launch_kwargs = {"headless": False, "args": CHROME_ARGS}
            if executable:
                launch_kwargs["executable_path"] = executable
            browser = await pw.chromium.launch(**launch_kwargs)
            try:
                context = await browser.new_context(
                    storage_state=str(cookies_path),
                    permissions=["geolocation"],
                    geolocation={"latitude": 39.9042, "longitude": 116.4074},  # 北京
                    user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/120.0.0.0 Safari/537.36",
                    viewport={"width": 1920, "height": 1080},
                    locale="zh-CN",
                    timezone_id="Asia/Shanghai",
                    extra_http_headers={"Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8"},
                )
                await context.add_init_script(STEALTH_JS)
                page = await context.new_page()
                page.on("dialog", lambda dialog: asyncio.ensure_future(dialog.dismiss()))

                logger.info(f"打开抖音创作者上传页,上传 {video.name}")
                await page.goto(UPLOAD_URL, timeout=600_000, wait_until="domcontentloaded")
                # 等页面初始化:加载指示器出现后消失
                try:
                    await page.wait_for_selector(SEL_LOADING, timeout=5000)
                    await page.wait_for_selector(SEL_LOADING, state="hidden", timeout=30_000)
                except Exception:
                    logger.info("未检测到加载指示器或加载已完成")
                await page.wait_for_timeout(10_000)

                # 上传视频文件并等待进度走完
                await page.locator(SEL_FILE_INPUT).set_input_files(str(video))
                logger.info("等待视频上传完成...")
                start = time.time()
                while True:
                    if time.time() - start > UPLOAD_TIMEOUT:
                        logger.error("视频上传超时")
                        return False
                    progress_box = page.locator(SEL_PROGRESS_CONTAINER)
                    if await progress_box.count():
                        try:
                            text = (
                                (await page.locator(SEL_PROGRESS_TEXT).first.inner_text(timeout=3000))
                                .strip()
                                .rstrip("%")
                            )
                            if text.isdigit():
                                logger.info(f"上传进度: {text}%")
                        except Exception:
                            pass
                        if await page.locator(SEL_REUPLOAD_INPUT).count():
                            break
                    elif await _check_upload_complete(page):
                        break
                    err = page.locator(SEL_ERROR_MESSAGE)
                    if await err.count():
                        logger.error(f"上传出错: {await err.first.inner_text()}")
                        return False
                    await asyncio.sleep(2)
                logger.info("视频上传完成,填写发布信息...")

                if cover:
                    await _set_thumbnail(page, cover)

                # 标题
                title_input = page.locator(SEL_TITLE_INPUT)
                await title_input.wait_for(state="visible", timeout=30_000)
                await title_input.click()
                await title_input.fill(title)

                # 话题标签:#tag + 空格逐个添加
                tag_container = page.locator(SEL_TAG_CONTAINER)
                await tag_container.wait_for(state="visible")
                for tag in tags:
                    await tag_container.type(f"#{tag}")
                    await page.keyboard.press("Space")
                    await asyncio.sleep(0.5)

                # 定时发布(可选)
                if publish_date:
                    try:
                        await _set_schedule_time(page, publish_date)
                    except Exception as e:
                        logger.warning(f"定时发布设置失败,改为立即发布: {e}")

                await _simulate_human_behavior(page)

                # 发布前保存最新 cookie(登录态续期)
                try:
                    await context.storage_state(path=str(cookies_path))
                except Exception as e:
                    logger.warning(f"保存 cookie 失败: {e}")

                logger.info("点击发布按钮...")
                publish_button = page.get_by_role("button", name="发布", exact=True)
                if not await publish_button.count():
                    logger.error("未找到发布按钮(平台页面可能已改版,需校准选择器)")
                    return False
                await asyncio.sleep(5)
                await publish_button.click()

                return await _wait_publish_result(page, context, cookies_path)
            finally:
                await browser.close()

    def upload(
        self,
        video: Path,
        title: str,
        tags: list[str],
        cover: Path | None,
        cookies_path: Path,
        publish_date: datetime | None = None,
    ) -> dict:
        self.validate_cookies(cookies_path)
        if not video.exists():
            raise FileNotFoundError(f"待上传视频不存在: {video}")
        title = truncate_title(self.platform, title)
        tags = normalize_tags(self.platform, tags)
        try:
            ok = asyncio.run(self._upload_async(video, title, tags, cover, cookies_path, publish_date))
        except Exception as e:
            logger.exception("抖音上传过程出错")
            return {"platform": self.platform, "ok": False, "detail": f"上传异常: {e}"}
        if ok:
            return {"platform": self.platform, "ok": True, "detail": "发布成功"}
        return {"platform": self.platform, "ok": False, "detail": "未检测到发布成功标识(详见日志,可能需校准选择器)"}


LOGIN_URL = "https://creator.douyin.com/creator-micro/content/upload"
LOGIN_WAIT_TIMEOUT = 300  # 等用户完成登录的上限(秒)


async def _login_douyin_async(cookies_path: Path) -> None:
    """直接弹出 Chrome 打开创作者平台,等用户扫码/验证登录后自动保存 storage_state。

    判登录成功:context cookie 中出现 douyin.com 域的 sessionid/sessionid_ss。
    """
    from playwright.async_api import async_playwright

    async with async_playwright() as pw:
        executable = _find_chrome_executable()
        launch_kwargs = {"headless": False, "args": CHROME_ARGS}
        if executable:
            launch_kwargs["executable_path"] = executable
        browser = await pw.chromium.launch(**launch_kwargs)
        try:
            context = await browser.new_context(
                viewport={"width": 1920, "height": 1080},
                locale="zh-CN",
                timezone_id="Asia/Shanghai",
            )
            await context.add_init_script(STEALTH_JS)
            page = await context.new_page()
            await page.goto(LOGIN_URL, timeout=120_000, wait_until="domcontentloaded")

            logger.info(
                "浏览器已打开抖音创作者平台,请扫码/验证登录,"
                f"登录成功后将自动保存登录态(最长等待 {LOGIN_WAIT_TIMEOUT} 秒)..."
            )
            deadline = time.time() + LOGIN_WAIT_TIMEOUT
            while time.time() < deadline:
                if browser.contexts and not context.pages:
                    raise RuntimeError("浏览器窗口被关闭,登录未完成")
                cookies = await context.cookies()
                if any(c["name"] in ("sessionid", "sessionid_ss") and "douyin.com" in c["domain"] for c in cookies):
                    break
                await asyncio.sleep(2)
            else:
                raise RuntimeError(f"等待登录超时({LOGIN_WAIT_TIMEOUT} 秒),请重试")

            # 等页面跳转完成、localStorage 写全后再存盘
            await asyncio.sleep(5)
            cookies_path.parent.mkdir(parents=True, exist_ok=True)
            await context.storage_state(path=str(cookies_path))
            saved = json.loads(cookies_path.read_text(encoding="utf-8"))
            logger.info(f"抖音登录态已保存({len(saved.get('cookies', []))} 条 cookie): {cookies_path}")
        finally:
            await browser.close()


def login_douyin(cookies_path: Path) -> None:
    """弹浏览器引导用户登录抖音并保存登录态(供 CLI login 子命令调用)。"""
    try:
        asyncio.run(_login_douyin_async(Path(cookies_path)))
    except Exception as e:
        raise RuntimeError(f"获取抖音登录态失败: {e}") from e
