"""输出写入器抽象基类"""

from abc import ABC, abstractmethod
from typing import List
from ..models import BiddingRecord


class OutputWriter(ABC):
    """输出写入器基类"""

    @abstractmethod
    def write(self, records: List[BiddingRecord], output_path: str) -> str:
        """
        将分析结果写入目标

        Args:
            records: 分析后的招中标记录列表
            output_path: 输出路径

        Returns:
            输出文件的完整路径
        """
        pass
