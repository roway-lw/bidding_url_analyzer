"""核心分析编排器"""

import asyncio
import time
from datetime import datetime
from typing import List, Callable, Optional
from .models import BiddingRecord, FetchResult, LinkStatus, AnalysisProgress
from .fetcher.httpx_fetcher import HttpxFetcher
from .extractor.summary_extractor import SummaryExtractor


class BiddingAnalyzer:
    """招中标链接分析器 - 核心编排"""

    def __init__(
        self,
        concurrency: int = 10,
        timeout: float = 15.0,
        use_playwright_fallback: bool = False,
        # LLM 配置
        llm_api_key: str = "",
        llm_base_url: str = "",
        llm_model: str = "",
        llm_concurrency: int = 5,
    ):
        self.concurrency = concurrency
        self.timeout = timeout
        self.use_playwright_fallback = use_playwright_fallback
        self.fetcher = HttpxFetcher(timeout=timeout)
        self.extractor = SummaryExtractor()
        self.progress = AnalysisProgress()

        # LLM 提取器（按需初始化）
        self._llm_extractor = None
        self._llm_api_key = llm_api_key
        self._llm_base_url = llm_base_url
        self._llm_model = llm_model
        self._llm_concurrency = llm_concurrency

        # 进度回调（用于SSE推送等）
        self._on_progress: Optional[Callable] = None
        self._on_record_done: Optional[Callable] = None

    @property
    def llm_extractor(self):
        """懒加载LLM提取器"""
        if self._llm_extractor is None and self._llm_api_key:
            from .extractor.llm_extractor import LLMExtractor
            self._llm_extractor = LLMExtractor(
                base_url=self._llm_base_url or "https://open.bigmodel.cn/api/anthropic",
                api_key=self._llm_api_key,
                model=self._llm_model or "glm-5.1",
                concurrency=self._llm_concurrency,
            )
        return self._llm_extractor

    def on_progress(self, callback: Callable):
        """注册进度回调"""
        self._on_progress = callback

    def on_record_done(self, callback: Callable):
        """注册单条记录完成回调"""
        self._on_record_done = callback

    def _notify_progress(self):
        if self._on_progress:
            self._on_progress(self.progress)

    def _notify_record_done(self, record: BiddingRecord):
        if self._on_record_done:
            self._on_record_done(record)

    async def analyze(self, records: List[BiddingRecord]) -> List[BiddingRecord]:
        """分析所有招中标记录"""
        self.progress = AnalysisProgress(
            total=len(records),
            start_time=datetime.now(),
            is_running=True,
        )
        self._notify_progress()

        semaphore = asyncio.Semaphore(self.concurrency)

        async def _analyze_one(record: BiddingRecord) -> BiddingRecord:
            async with semaphore:
                return await self._analyze_record(record)

        # 并发执行所有分析任务
        tasks = [_analyze_one(r) for r in records]
        results = await asyncio.gather(*tasks)

        self.progress.is_running = False
        self.progress.is_finished = True
        self._notify_progress()

        return list(results)

    async def _analyze_record(self, record: BiddingRecord) -> BiddingRecord:
        """分析单条记录"""
        self.progress.current_url = record.原文链接
        self._notify_progress()

        url = record.原文链接.strip()
        if not url:
            record.链接状态 = LinkStatus.INVALID
            record.错误信息 = "链接为空"
            record.检测时间 = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            self._update_counts(record)
            self._notify_record_done(record)
            return record

        # 1. URL抓取
        fetch_result = await self.fetcher.fetch(url)

        # 2. 设置链接状态
        record.链接状态 = fetch_result.link_status
        record.错误信息 = fetch_result.error
        record.检测时间 = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        # 3. 如果有效，提取摘要
        if record.链接状态 == LinkStatus.VALID:
            if self.llm_extractor:
                # 使用LLM增强提取
                summary = await self.llm_extractor.extract_async(record, fetch_result)
                # 同步token统计到progress
                self.progress.total_input_tokens = self.llm_extractor.total_input_tokens
                self.progress.total_output_tokens = self.llm_extractor.total_output_tokens
            else:
                # 纯规则提取
                summary = self.extractor.extract(record, fetch_result)
            record.页面摘要 = summary.to_text()
            # AI摘要单独存储（LLM生成的项目摘要段落）
            if summary.项目摘要:
                record.AI摘要 = summary.项目摘要

        self._update_counts(record)
        self.progress.completed += 1
        self._notify_progress()
        self._notify_record_done(record)

        return record

    def _update_counts(self, record: BiddingRecord):
        """更新分类计数"""
        if record.链接状态 == LinkStatus.VALID:
            self.progress.valid_count += 1
        elif record.链接状态 == LinkStatus.NEEDS_LOGIN:
            self.progress.login_count += 1
        elif record.链接状态 == LinkStatus.INVALID:
            self.progress.invalid_count += 1

    def analyze_sync(self, records: List[BiddingRecord]) -> List[BiddingRecord]:
        """同步版本的分析方法"""
        return asyncio.run(self.analyze(records))
