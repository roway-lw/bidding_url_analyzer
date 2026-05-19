"""大模型驱动的摘要提取器 - 使用 Anthropic 兼容接口"""

import json
import asyncio
from typing import Optional
from ..models import SummaryResult, BiddingRecord, FetchResult, LinkStatus

# 默认LLM配置
DEFAULT_BASE_URL = "https://open.bigmodel.cn/api/anthropic"
DEFAULT_MODEL = "glm-5.1"

EXTRACTION_PROMPT = """你是一个招标/中标信息提取专家。请从以下网页内容中提取招中标关键信息，以JSON格式返回。

需要提取的字段：
- 项目名称：项目/标讯的完整名称
- 招标编号：项目编号/招标编号
- 招标业主：采购单位/招标方
- 中标单位：中标方/成交供应商
- 中标金额：中标/成交金额（请转换为元为单位，纯数字，如无则为0）
- 所属地区：项目所在省份+城市
- 发布时间：公告发布日期（格式：YYYY-MM-DD）
- 项目类型：项目所属类别（如工程建设、货物采购、服务等）
- 项目摘要：用2-3句话概括项目核心内容，包括采购内容、规模、关键要求等

请严格按以下JSON格式返回，不要添加任何其他文字：
{
  "项目名称": "",
  "招标编号": "",
  "招标业主": "",
  "中标单位": "",
  "中标金额": 0,
  "所属地区": "",
  "发布时间": "",
  "项目类型": "",
  "项目摘要": ""
}

如果某个字段无法提取，保持空字符串（金额为0）。

网页内容：
"""


class LLMExtractor:
    """大模型驱动的摘要提取器"""

    def __init__(
        self,
        base_url: str = DEFAULT_BASE_URL,
        api_key: str = "",
        model: str = DEFAULT_MODEL,
        max_text_length: int = 4000,
        concurrency: int = 5,
    ):
        self.base_url = base_url
        self.api_key = api_key
        self.model = model
        self.max_text_length = max_text_length
        self._semaphore = asyncio.Semaphore(concurrency)
        self._client = None
        # Token 统计
        self.total_input_tokens = 0
        self.total_output_tokens = 0

    def _get_client(self):
        """懒加载 Anthropic 客户端"""
        if self._client is None:
            try:
                import anthropic
                self._client = anthropic.Anthropic(
                    base_url=self.base_url,
                    api_key=self.api_key,
                )
            except ImportError:
                raise ImportError("请安装 anthropic 包: pip install anthropic")
        return self._client

    async def extract_async(
        self,
        record: BiddingRecord,
        fetch_result: Optional[FetchResult] = None,
    ) -> SummaryResult:
        """异步提取：先用规则提取基础，再用LLM增强"""
        # 先用规则提取器获取基础结果
        from .summary_extractor import SummaryExtractor
        rule_extractor = SummaryExtractor()
        result = rule_extractor.extract(record, fetch_result)

        # 只对有效链接且有网页内容的记录调用LLM
        if not self.api_key:
            return result
        if fetch_result is None or record.链接状态 != LinkStatus.VALID:
            return result
        if not fetch_result.text or len(fetch_result.text.strip()) < 50:
            return result

        async with self._semaphore:
            try:
                llm_result = await self._call_llm(record, fetch_result.text)
                if llm_result:
                    # LLM结果优先级更高，但保留规则已提取的非空字段
                    self._merge_results(result, llm_result)
            except Exception as e:
                # LLM调用失败不影响整体结果
                print(f"[LLM] 提取失败 (index={record.index}): {e}")

        return result

    async def _call_llm(self, record: BiddingRecord, text: str) -> Optional[dict]:
        """调用大模型提取结构化信息"""
        # 截断过长文本
        page_text = text[:self.max_text_length] if len(text) > self.max_text_length else text

        prompt = EXTRACTION_PROMPT + page_text

        client = self._get_client()

        # 在线程池中执行同步API调用
        loop = asyncio.get_event_loop()
        response = await loop.run_in_executor(
            None,
            lambda: client.messages.create(
                model=self.model,
                max_tokens=1024,
                messages=[{"role": "user", "content": prompt}],
            )
        )

        # 统计token用量
        if hasattr(response, 'usage'):
            self.total_input_tokens += getattr(response.usage, 'input_tokens', 0)
            self.total_output_tokens += getattr(response.usage, 'output_tokens', 0)

        # 解析响应
        content = ""
        for block in response.content:
            if hasattr(block, "text"):
                content += block.text

        return self._parse_json_response(content)

    def _parse_json_response(self, content: str) -> Optional[dict]:
        """解析LLM返回的JSON"""
        content = content.strip()
        # 尝试提取JSON块
        if "```json" in content:
            start = content.index("```json") + 7
            end = content.index("```", start)
            content = content[start:end].strip()
        elif "```" in content:
            start = content.index("```") + 3
            end = content.index("```", start)
            content = content[start:end].strip()
        elif "{" in content and "}" in content:
            start = content.index("{")
            end = content.rindex("}") + 1
            content = content[start:end]

        try:
            data = json.loads(content)
            # 验证必要字段
            expected_keys = {"项目名称", "招标编号", "招标业主", "中标单位", "中标金额", "所属地区", "发布时间", "项目类型", "项目摘要"}
            if any(k in data for k in expected_keys):
                return data
        except json.JSONDecodeError:
            pass

        return None

    def _merge_results(self, base: SummaryResult, llm_data: dict):
        """将LLM提取结果合并到基础结果（LLM非空值覆盖）"""
        # 项目名称
        v = llm_data.get("项目名称", "")
        if v and v != "无":
            base.项目名称 = v

        # 招标编号
        v = llm_data.get("招标编号", "")
        if v and v != "无":
            base.招标编号 = v

        # 招标业主
        v = llm_data.get("招标业主", "")
        if v and v != "无":
            base.招标业主 = v

        # 中标单位
        v = llm_data.get("中标单位", "")
        if v and v != "无":
            base.中标单位 = v

        # 中标金额 - LLM返回数字，格式化为字符串
        v = llm_data.get("中标金额", 0)
        if v and isinstance(v, (int, float)) and v > 0:
            if v >= 10000:
                base.中标金额 = f"{v / 10000:.2f}万元"
            else:
                base.中标金额 = f"{v:.2f}元"
        elif isinstance(v, str) and v and v != "无":
            base.中标金额 = v

        # 所属地区
        v = llm_data.get("所属地区", "")
        if v and v != "无":
            base.所属地区 = v

        # 发布时间
        v = llm_data.get("发布时间", "")
        if v and v != "无":
            base.发布时间 = v

        # 项目类型
        v = llm_data.get("项目类型", "")
        if v and v != "无":
            base.项目类型 = v

        # 项目摘要 - 这是LLM最大的价值点
        v = llm_data.get("项目摘要", "")
        if v and v != "无":
            base.项目摘要 = v
