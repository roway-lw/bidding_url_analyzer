"""输入适配器抽象基类"""

from abc import ABC, abstractmethod
from typing import List
from ..models import BiddingRecord


class InputAdapter(ABC):
    """输入适配器基类，所有输入源必须实现此接口"""

    @abstractmethod
    def load(self, source: str) -> List[BiddingRecord]:
        """
        从数据源加载招中标记录

        Args:
            source: 数据源路径或连接字符串

        Returns:
            招中标记录列表
        """
        pass

    @abstractmethod
    def get_record_count(self, source: str) -> int:
        """获取记录总数"""
        pass
