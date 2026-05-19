"""数据模型定义"""

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional
from datetime import datetime


class LinkStatus(str, Enum):
    """链接状态三分类"""
    VALID = "有效"
    NEEDS_LOGIN = "需登录查看"
    INVALID = "链接无效"


@dataclass
class BiddingRecord:
    """招中标记录数据模型"""
    # 原始字段
    index: int = 0
    标讯标题: str = ""
    原文链接: str = ""
    来源网站名称: str = ""
    招中标类型: str = ""
    招中标阶段: str = ""
    采购单位: str = ""
    中标单位: str = ""
    中标金额: float = 0.0
    招标金额: float = 0.0
    省份: str = ""
    城市: str = ""
    项目名称: str = ""
    项目大类: str = ""
    招标编号: str = ""
    发布时间: str = ""
    # 原始数据字典（保留全部字段）
    raw_data: dict = field(default_factory=dict)

    # 分析结果字段
    链接状态: LinkStatus = LinkStatus.VALID
    页面摘要: str = ""
    AI摘要: str = ""  # 大模型生成的摘要段落（独立于结构化字段）
    检测时间: str = ""
    错误信息: str = ""


@dataclass
class FetchResult:
    """URL抓取结果"""
    url: str = ""
    status_code: int = 0
    content_type: str = ""
    html: str = ""
    text: str = ""
    error: str = ""
    elapsed: float = 0.0
    link_status: LinkStatus = LinkStatus.VALID


@dataclass
class SummaryResult:
    """摘要提取结果"""
    项目名称: str = ""
    招标编号: str = ""
    招标业主: str = ""
    中标单位: str = ""
    中标金额: str = ""
    所属地区: str = ""
    发布时间: str = ""
    项目类型: str = ""
    数据来源: str = ""
    项目摘要: str = ""
    公告类型: str = ""

    def to_text(self) -> str:
        """格式化为结构化文本摘要"""
        lines = []
        type_label = self.公告类型 or "中标公告"
        title = self.项目名称 or "未知项目"
        lines.append(f"【{type_label}】{title}")
        if self.招标编号:
            lines.append(f"- 招标编号：{self.招标编号}")
        if self.招标业主:
            lines.append(f"- 招标业主：{self.招标业主}")
        if self.中标单位:
            lines.append(f"- 中标单位：{self.中标单位}")
        if self.中标金额:
            lines.append(f"- 中标金额：{self.中标金额}")
        if self.所属地区:
            lines.append(f"- 所属地区：{self.所属地区}")
        if self.发布时间:
            lines.append(f"- 发布时间：{self.发布时间}")
        if self.项目类型:
            lines.append(f"- 项目类型：{self.项目类型}")
        if self.数据来源:
            lines.append(f"- 数据来源：{self.数据来源}")
        return "\n".join(lines)


@dataclass
class AnalysisProgress:
    """分析进度"""
    total: int = 0
    completed: int = 0
    valid_count: int = 0
    login_count: int = 0
    invalid_count: int = 0
    current_url: str = ""
    start_time: Optional[datetime] = None
    is_running: bool = False
    is_finished: bool = False
    # Token 统计
    total_input_tokens: int = 0
    total_output_tokens: int = 0
