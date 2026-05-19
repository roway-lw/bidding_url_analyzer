"""招中标信息摘要提取器"""

import re
from typing import Optional
from bs4 import BeautifulSoup, Tag
from ..models import SummaryResult, BiddingRecord, FetchResult, LinkStatus


class SummaryExtractor:
    """规则驱动的摘要提取器"""

    # 金额匹配模式
    AMOUNT_PATTERNS = [
        r'(?:中标金额|成交金额|合同金额|预算金额|项目金额)[：:]\s*([\d,.]+)\s*(?:万?元)',
        r'(?:人民币)[：:]\s*([\d,.]+)\s*(?:万?元)',
        r'([\d,.]+)\s*万?元',
    ]

    # 招标编号模式
    BID_NO_PATTERNS = [
        r'(?:招标编号|项目编号|标段编号|采购编号|编号)[：:]\s*([A-Za-z0-9\-_]+)',
        r'(?:标段|包)[：:]\s*([A-Za-z0-9\-_]+)',
    ]

    # 日期模式
    DATE_PATTERNS = [
        r'(\d{4})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日',
        r'(\d{4})[/-](\d{1,2})[/-](\d{1,2})',
        r'发布时间[：:]\s*(\d{4}[/-]\d{1,2}[/-]\d{1,2})',
    ]

    def extract(
        self,
        record: BiddingRecord,
        fetch_result: Optional[FetchResult] = None,
    ) -> SummaryResult:
        """
        从BiddingRecord和FetchResult中提取结构化摘要

        优先使用Excel已有的结构化字段，不足部分从网页内容补充
        """
        result = SummaryResult()

        # 从原始记录填充已知字段
        result.项目名称 = record.标讯标题 or record.项目名称 or ""
        result.招标编号 = record.招标编号 or ""
        result.招标业主 = record.采购单位 or ""
        result.中标单位 = record.中标单位 or ""
        result.所属地区 = f"{record.省份}{record.城市}" if record.省份 else ""
        result.发布时间 = record.发布时间 or ""
        result.项目类型 = record.项目大类 or ""
        result.数据来源 = record.来源网站名称 or ""
        result.公告类型 = record.招中标阶段 or "中标公告"

        # 格式化中标金额
        if record.中标金额 and record.中标金额 > 0:
            if record.中标金额 >= 10000:
                result.中标金额 = f"{record.中标金额 / 10000:.2f}万元"
            else:
                result.中标金额 = f"{record.中标金额:.2f}元"

        # 如果链接无效或需登录，不进行网页内容提取
        if fetch_result is None or record.链接状态 != LinkStatus.VALID:
            return result

        # 从网页内容补充缺失字段
        html = fetch_result.html
        text = fetch_result.text

        if not html and not text:
            return result

        # 补充缺失字段
        if not result.招标编号:
            result.招标编号 = self._extract_bid_no(text)

        if not result.发布时间:
            result.发布时间 = self._extract_date(text)

        if not result.中标金额:
            result.中标金额 = self._extract_amount(text)

        # 从页面内容生成项目摘要
        result.项目摘要 = self._extract_project_summary(text, record)

        return result

    def _extract_bid_no(self, text: str) -> str:
        """提取招标编号"""
        for pattern in self.BID_NO_PATTERNS:
            match = re.search(pattern, text)
            if match:
                return match.group(1)
        return ""

    def _extract_date(self, text: str) -> str:
        """提取日期"""
        for pattern in self.DATE_PATTERNS:
            match = re.search(pattern, text)
            if match:
                if len(match.groups()) == 3:
                    return f"{match.group(1)}-{match.group(2).zfill(2)}-{match.group(3).zfill(2)}"
                return match.group(1)
        return ""

    def _extract_amount(self, text: str) -> str:
        """提取金额"""
        for pattern in self.AMOUNT_PATTERNS:
            match = re.search(pattern, text)
            if match:
                amount_str = match.group(1).replace(",", "")
                try:
                    amount = float(amount_str)
                    if "万" in match.group(0):
                        return f"{amount:.2f}万元"
                    elif amount >= 10000:
                        return f"{amount / 10000:.2f}万元"
                    else:
                        return f"{amount:.2f}元"
                except ValueError:
                    continue
        return ""

    def _extract_project_summary(self, text: str, record: BiddingRecord) -> str:
        """生成项目摘要"""
        # 取页面文本的前300字作为摘要基础
        summary_text = text[:500] if text else ""

        # 尝试提取与项目相关的句子
        keywords = ["采购", "招标", "中标", "项目", "工程", "建设", "供应", "服务"]
        sentences = re.split(r'[。；\n]', summary_text)

        relevant = []
        for s in sentences:
            s = s.strip()
            if len(s) > 10 and any(kw in s for kw in keywords):
                relevant.append(s)
            if len(relevant) >= 3:
                break

        if relevant:
            return "。".join(relevant) + "。"

        # 回退：取前200字
        clean = re.sub(r'\s+', ' ', summary_text).strip()
        if len(clean) > 200:
            return clean[:200] + "..."
        return clean
