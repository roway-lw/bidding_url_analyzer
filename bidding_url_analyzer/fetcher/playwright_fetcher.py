"""基于Playwright的重型URL抓取器（JS渲染页面降级方案）"""

import time
from typing import Optional
from ..models import FetchResult, LinkStatus


class PlaywrightFetcher:
    """基于Playwright的浏览器抓取器"""

    def __init__(self, timeout: float = 30.0, headless: bool = True):
        self.timeout = timeout
        self.headless = headless
        self._browser = None
        self._playwright = None

    async def _ensure_browser(self):
        """延迟初始化浏览器"""
        if self._browser is None:
            try:
                from playwright.async_api import async_playwright
                self._playwright = await async_playwright().start()
                self._browser = await self._playwright.chromium.launch(
                    headless=self.headless
                )
            except ImportError:
                raise RuntimeError(
                    "Playwright未安装，请运行: pip install playwright && playwright install chromium"
                )

    async def fetch(self, url: str) -> FetchResult:
        """使用Playwright抓取页面"""
        start_time = time.time()
        result = FetchResult(url=url)

        try:
            await self._ensure_browser()
            page = await self._browser.new_page()

            try:
                response = await page.goto(
                    url, wait_until="domcontentloaded", timeout=self.timeout * 1000
                )

                if response:
                    result.status_code = response.status
                    result.content_type = response.headers.get("content-type", "")

                    # 等待页面渲染
                    await page.wait_for_timeout(2000)

                    result.html = await page.content()

                    # 提取纯文本
                    result.text = await page.inner_text("body")

                    # 分类判断（复用httpx_fetcher的逻辑）
                    from .httpx_fetcher import HttpxFetcher
                    fetcher = HttpxFetcher()
                    result.link_status = fetcher._classify(result)

                else:
                    result.link_status = LinkStatus.INVALID
                    result.error = "无响应"

            finally:
                await page.close()

        except Exception as e:
            result.link_status = LinkStatus.INVALID
            result.error = f"Playwright异常: {type(e).__name__}"

        result.elapsed = time.time() - start_time
        return result

    async def close(self):
        """关闭浏览器"""
        if self._browser:
            await self._browser.close()
        if self._playwright:
            await self._playwright.stop()
