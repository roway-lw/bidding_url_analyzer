"""基于httpx的轻量级URL抓取器"""

import asyncio
import time
from typing import Optional
import httpx
from ..models import FetchResult, LinkStatus


# 需要登录/付费的关键词
LOGIN_KEYWORDS = [
    "请先登录", "用户登录", "请登录", "登录后", "登录查看",
    "会员", "VIP", "付费查看", "充值", "开通会员",
    "需要登录", "注册后", "验证码", "扫码登录",
    "账号登录", "短信验证", "购买后查看",
]

# 页面内容有效性的最小文本长度（低于此值可能为空页面或重定向页）
MIN_CONTENT_LENGTH = 200


class HttpxFetcher:
    """轻量级HTTP抓取器"""

    def __init__(
        self,
        timeout: float = 15.0,
        max_redirects: int = 5,
        headers: Optional[dict] = None,
    ):
        self.timeout = timeout
        self.max_redirects = max_redirects
        self.default_headers = headers or {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        }

    async def fetch(self, url: str) -> FetchResult:
        """抓取单个URL"""
        start_time = time.time()
        result = FetchResult(url=url)

        try:
            async with httpx.AsyncClient(
                timeout=httpx.Timeout(self.timeout, connect=5.0),
                max_redirects=self.max_redirects,
                follow_redirects=True,
                verify=False,
            ) as client:
                # 先尝试HEAD请求快速判断
                try:
                    head_resp = await client.head(url, headers=self.default_headers)
                    result.status_code = head_resp.status_code
                    result.content_type = head_resp.headers.get("content-type", "")

                    # 非HTML内容直接标记
                    if result.status_code >= 400:
                        result.link_status = LinkStatus.INVALID
                        result.error = f"HTTP {result.status_code}"
                        result.elapsed = time.time() - start_time
                        return result

                    # 如果不是HTML，不再GET
                    if "text/html" not in result.content_type and "text/plain" not in result.content_type:
                        # PDF等文件视为有效
                        if "pdf" in result.content_type or "application" in result.content_type:
                            result.link_status = LinkStatus.VALID
                        else:
                            result.link_status = LinkStatus.VALID
                        result.elapsed = time.time() - start_time
                        return result
                except httpx.TimeoutException:
                    result.link_status = LinkStatus.INVALID
                    result.error = "请求超时"
                    result.elapsed = time.time() - start_time
                    return result
                except httpx.ConnectError:
                    result.link_status = LinkStatus.INVALID
                    result.error = "连接失败"
                    result.elapsed = time.time() - start_time
                    return result
                except Exception:
                    pass  # HEAD失败，尝试GET

                # GET请求获取内容
                try:
                    get_resp = await client.get(url, headers=self.default_headers)
                    result.status_code = get_resp.status_code
                    result.content_type = get_resp.headers.get("content-type", "")
                    result.html = get_resp.text

                    # 提取纯文本（去掉标签）
                    from bs4 import BeautifulSoup
                    soup = BeautifulSoup(result.html, "html.parser")

                    # 移除script和style
                    for tag in soup(["script", "style", "nav", "footer", "header"]):
                        tag.decompose()

                    result.text = soup.get_text(separator=" ", strip=True)

                    # 判断三分类
                    result.link_status = self._classify(result)

                except httpx.TimeoutException:
                    result.link_status = LinkStatus.INVALID
                    result.error = "GET请求超时"
                except httpx.ConnectError:
                    result.link_status = LinkStatus.INVALID
                    result.error = "GET连接失败"
                except httpx.TooManyRedirects:
                    result.link_status = LinkStatus.INVALID
                    result.error = "重定向次数过多"
                except Exception as e:
                    result.link_status = LinkStatus.INVALID
                    result.error = f"请求异常: {type(e).__name__}"

        except Exception as e:
            result.link_status = LinkStatus.INVALID
            result.error = f"客户端异常: {type(e).__name__}"

        result.elapsed = time.time() - start_time
        return result

    def _classify(self, result: FetchResult) -> LinkStatus:
        """三分类判断逻辑"""
        # 1. HTTP状态码判断
        if result.status_code >= 400:
            return LinkStatus.INVALID

        # 2. 内容为空
        if not result.text or len(result.text) < MIN_CONTENT_LENGTH:
            # 内容太短可能是JS渲染页面或空页面
            # 检查HTML中是否有登录表单
            html_lower = result.html.lower()
            if any(kw in html_lower for kw in ["login", "signin", "sign-in", "登录"]):
                return LinkStatus.NEEDS_LOGIN
            # 内容极短，可能是空壳页面
            if len(result.text) < 50:
                return LinkStatus.INVALID
            return LinkStatus.NEEDS_LOGIN

        # 3. 检测登录/付费墙关键词
        text_lower = result.text.lower()
        html_lower = result.html.lower()

        # 在页面文本中检查
        login_score = 0
        for kw in LOGIN_KEYWORDS:
            if kw in result.text:
                login_score += 1

        # 在HTML中检查表单和特殊元素
        if 'type="password"' in html_lower or 'type="password"' in html_lower:
            login_score += 2
        if "captcha" in html_lower or "验证码" in result.text:
            login_score += 2

        if login_score >= 3:
            return LinkStatus.NEEDS_LOGIN

        return LinkStatus.VALID

    async def fetch_batch(
        self, urls: list[str], concurrency: int = 10
    ) -> list[FetchResult]:
        """批量抓取URL"""
        semaphore = asyncio.Semaphore(concurrency)

        async def _fetch_with_semaphore(url: str) -> FetchResult:
            async with semaphore:
                return await self.fetch(url)

        tasks = [_fetch_with_semaphore(url) for url in urls]
        return await asyncio.gather(*tasks)
