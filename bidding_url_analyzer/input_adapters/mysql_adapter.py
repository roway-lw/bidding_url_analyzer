"""MySQL输入适配器 - 从MySQL数据库读取招中标记录"""

from typing import List, Optional
import pymysql
from pymysql.constants import CLIENT
from .base import InputAdapter
from ..models import BiddingRecord


# Excel列名到模型字段的映射（与ExcelAdapter保持一致）
COLUMN_MAPPING = {
    "标讯标题": "标讯标题",
    "原文链接": "原文链接",
    "来源网站名称": "来源网站名称",
    "招中标类型": "招中标类型",
    "招中标阶段": "招中标阶段",
    "采购单位": "采购单位",
    "中标单位": "中标单位",
    "中标金额": "中标金额",
    "招标金额": "招标金额",
    "省份": "省份",
    "城市": "城市",
    "项目名称": "项目名称",
    "项目大类": "项目大类",
    "招标编号": "招标编号",
    "发布时间": "发布时间",
    # 英文列名映射
    "title": "标讯标题",
    "url": "原文链接",
    "purchaser": "采购单位",
    "bidder": "中标单位",
    "bid_price": "中标金额",
    # 常见变体
    "link_url": "原文链接",
    "source": "来源网站名称",
    "bid_amount": "中标金额",
    "tender_amount": "招标金额",
    "province": "省份",
    "city": "城市",
    "project_name": "项目名称",
    "bid_no": "招标编号",
    "publish_time": "发布时间",
    "publish_date": "发布时间",
}

# 默认连接超时（秒）
DEFAULT_CONNECT_TIMEOUT = 10
# 默认读写超时（秒）
DEFAULT_READ_TIMEOUT = 30


def _create_connection(
    host: str, port: int, user: str, password: str,
    database: str = None, charset: str = "utf8mb4",
    connect_timeout: int = DEFAULT_CONNECT_TIMEOUT,
    read_timeout: int = DEFAULT_READ_TIMEOUT,
):
    """
    创建MySQL连接，自动处理常见兼容性问题：
    1. localhost -> 127.0.0.1 （避免Windows IPv6解析问题）
    2. MySQL 8.0+ caching_sha2_password 认证兼容
    3. 合理的超时设置
    """
    # Windows上localhost可能解析到IPv6 ::1，而MySQL通常只监听IPv4
    actual_host = host
    if host == "localhost":
        actual_host = "127.0.0.1"

    # 确保port是整数
    port = int(port)

    # 尝试连接，依次尝试不同认证方式
    errors = []

    # 第一次尝试：默认方式（pymysql 1.1+ 支持caching_sha2_password）
    try:
        conn = pymysql.connect(
            host=actual_host,
            port=port,
            user=user,
            password=password,
            database=database or None,
            charset=charset,
            cursorclass=pymysql.cursors.DictCursor,
            connect_timeout=connect_timeout,
            read_timeout=read_timeout,
            client_flag=CLIENT.MULTI_STATEMENTS,
        )
        return conn
    except pymysql.err.OperationalError as e:
        error_code = e.args[0] if e.args else 0
        # 如果是认证错误（1045），尝试 mysql_native_password
        if error_code == 1045 or error_code == 2054 or error_code == 2061:
            errors.append(f"默认认证失败(错误码{error_code}): {e}")
        elif error_code == 2003 or error_code == 2002:
            # 连接被拒绝或找不到主机
            # 如果用的是127.0.0.1，再试试原始host
            if actual_host != host:
                try:
                    conn = pymysql.connect(
                        host=host,
                        port=port,
                        user=user,
                        password=password,
                        database=database or None,
                        charset=charset,
                        cursorclass=pymysql.cursors.DictCursor,
                        connect_timeout=connect_timeout,
                        read_timeout=read_timeout,
                    )
                    return conn
                except Exception as e2:
                    errors.append(f"连接 {host} 失败: {e2}")
                    errors.append(f"连接 {actual_host} 失败: {e}")
            else:
                errors.append(f"连接被拒绝: {e}")
        else:
            raise
    except Exception as e:
        errors.append(f"默认连接方式失败: {e}")

    # 第二次尝试：显式指定 mysql_native_password 认证
    try:
        conn = pymysql.connect(
            host=actual_host,
            port=port,
            user=user,
            password=password,
            database=database or None,
            charset=charset,
            cursorclass=pymysql.cursors.DictCursor,
            connect_timeout=connect_timeout,
            read_timeout=read_timeout,
            auth_plugin="mysql_native_password",
        )
        return conn
    except Exception as e:
        errors.append(f"mysql_native_password 认证失败: {e}")

    # 第三次尝试：如果还没试过原始host
    if actual_host != host:
        try:
            conn = pymysql.connect(
                host=host,
                port=port,
                user=user,
                password=password,
                database=database or None,
                charset=charset,
                cursorclass=pymysql.cursors.DictCursor,
                connect_timeout=connect_timeout,
                read_timeout=read_timeout,
                auth_plugin="mysql_native_password",
            )
            return conn
        except Exception as e:
            errors.append(f"原始host + mysql_native_password 失败: {e}")

    # 所有尝试都失败，抛出详细错误
    error_detail = "\n".join(errors)
    raise pymysql.err.OperationalError(
        2003,
        f"无法连接MySQL服务器 {host}:{port}。请检查：\n"
        f"1. MySQL服务是否已启动\n"
        f"2. 主机地址和端口是否正确\n"
        f"3. 用户名和密码是否正确\n"
        f"4. 防火墙是否放行了{port}端口\n"
        f"详细错误:\n{error_detail}"
    )


class MySQLAdapter(InputAdapter):
    """MySQL数据库输入适配器"""

    def __init__(
        self,
        host: str = "localhost",
        port: int = 3306,
        user: str = "root",
        password: str = "",
        database: str = "",
        charset: str = "utf8mb4",
    ):
        self.host = host
        self.port = int(port)
        self.user = user
        self.password = password
        self.database = database
        self.charset = charset

    def _get_connection(self, database: str = None):
        """获取数据库连接"""
        return _create_connection(
            host=self.host,
            port=self.port,
            user=self.user,
            password=self.password,
            database=database or self.database,
            charset=self.charset,
        )

    def load(self, source: str) -> List[BiddingRecord]:
        """
        从MySQL表读取招中标记录

        Args:
            source: JSON格式字符串，包含 table, url_column, pk_column 等参数
                    或者直接是表名（使用默认配置）
        """
        import json
        if source.startswith("{"):
            params = json.loads(source)
        else:
            params = {"table": source}

        table = params.get("table", "")
        url_column = params.get("url_column", "原文链接")
        pk_column = params.get("pk_column", "id")

        if not table:
            raise ValueError("必须指定表名")

        conn = self._get_connection()
        try:
            with conn.cursor() as cursor:
                # 获取表的列名
                cursor.execute(f"SHOW COLUMNS FROM `{table}`")
                columns_info = cursor.fetchall()
                db_columns = [col["Field"] for col in columns_info]

                # 构建列名映射：数据库列名 -> 模型字段名
                col_field_map = {}
                for db_col in db_columns:
                    if db_col in COLUMN_MAPPING:
                        col_field_map[db_col] = COLUMN_MAPPING[db_col]
                    elif db_col == url_column:
                        col_field_map[db_col] = "原文链接"
                    elif db_col == pk_column:
                        col_field_map[db_col] = "_pk"

                # 查询数据
                select_cols = ", ".join(f"`{c}`" for c in db_columns)
                cursor.execute(f"SELECT {select_cols} FROM `{table}`")
                rows = cursor.fetchall()

                records = []
                for row_idx, row in enumerate(rows, start=1):
                    raw_data = {}
                    pk_value = None
                    for db_col in db_columns:
                        val = row.get(db_col)
                        raw_data[db_col] = val
                        if db_col == pk_column:
                            pk_value = val

                    record = BiddingRecord(
                        index=row_idx,
                        raw_data=raw_data,
                    )

                    # 填充映射字段
                    for db_col, model_field in col_field_map.items():
                        if model_field == "_pk":
                            continue
                        val = row.get(db_col)
                        if model_field in ("中标金额", "招标金额"):
                            try:
                                val = float(val) if val else 0.0
                            except (ValueError, TypeError):
                                val = 0.0
                        else:
                            val = str(val) if val is not None else ""
                        setattr(record, model_field, val)

                    # 保存主键值到raw_data以便后续更新
                    if pk_value is not None:
                        record.raw_data["_pk_value"] = pk_value
                        record.raw_data["_pk_column"] = pk_column

                    # 如果原文链接为空则跳过
                    if not record.原文链接:
                        continue

                    records.append(record)

                return records
        finally:
            conn.close()

    def get_record_count(self, source: str) -> int:
        """获取记录总数"""
        import json
        if source.startswith("{"):
            params = json.loads(source)
        else:
            params = {"table": source}

        table = params.get("table", "")
        if not table:
            return 0

        conn = self._get_connection()
        try:
            with conn.cursor() as cursor:
                cursor.execute(f"SELECT COUNT(*) as cnt FROM `{table}`")
                result = cursor.fetchone()
                return result["cnt"] if result else 0
        finally:
            conn.close()

    @staticmethod
    def test_connection(host: str, port: int, user: str, password: str) -> dict:
        """测试数据库连接，返回详细的连接信息"""
        port = int(port)
        try:
            conn = _create_connection(
                host=host, port=port, user=user, password=password,
                connect_timeout=10,
            )
            # 获取MySQL版本和连接信息
            info = {}
            try:
                with conn.cursor() as cursor:
                    cursor.execute("SELECT VERSION() as version")
                    row = cursor.fetchone()
                    info["mysql_version"] = row["version"] if row else "unknown"

                    cursor.execute("SELECT DATABASE() as db")
                    row = cursor.fetchone()
                    info["current_database"] = row["db"] if row else None
            except Exception:
                pass

            conn.close()

            return {
                "success": True,
                "message": "连接成功",
                "host": host,
                "port": port,
                **info,
            }
        except Exception as e:
            return {
                "success": False,
                "message": str(e),
                "host": host,
                "port": port,
            }

    @staticmethod
    def list_databases(host: str, port: int, user: str, password: str) -> List[str]:
        """列出所有数据库"""
        port = int(port)
        conn = _create_connection(
            host=host, port=port, user=user, password=password,
        )
        try:
            with conn.cursor() as cursor:
                cursor.execute("SHOW DATABASES")
                results = cursor.fetchall()
                # 过滤系统库
                skip = {"information_schema", "mysql", "performance_schema", "sys"}
                return [r["Database"] for r in results if r["Database"] not in skip]
        finally:
            conn.close()

    @staticmethod
    def list_tables(host: str, port: int, user: str, password: str, database: str) -> List[str]:
        """列出指定数据库的所有表"""
        port = int(port)
        conn = _create_connection(
            host=host, port=port, user=user, password=password,
            database=database,
        )
        try:
            with conn.cursor() as cursor:
                cursor.execute("SHOW TABLES")
                results = cursor.fetchall()
                key = f"Tables_in_{database}"
                return [r[key] for r in results]
        finally:
            conn.close()

    @staticmethod
    def list_columns(host: str, port: int, user: str, password: str, database: str, table: str) -> List[dict]:
        """列出指定表的所有列"""
        port = int(port)
        conn = _create_connection(
            host=host, port=port, user=user, password=password,
            database=database,
        )
        try:
            with conn.cursor() as cursor:
                cursor.execute(f"SHOW COLUMNS FROM `{table}`")
                return cursor.fetchall()
        finally:
            conn.close()

    @staticmethod
    def preview_data(
        host: str, port: int, user: str, password: str,
        database: str, table: str, limit: int = 10,
    ) -> List[dict]:
        """预览表数据"""
        port = int(port)
        conn = _create_connection(
            host=host, port=port, user=user, password=password,
            database=database,
        )
        try:
            with conn.cursor() as cursor:
                cursor.execute(f"SELECT * FROM `{table}` LIMIT %s", (limit,))
                return cursor.fetchall()
        finally:
            conn.close()

    @staticmethod
    def update_results(
        host: str, port: int, user: str, password: str,
        database: str, table: str, pk_column: str,
        results: List[BiddingRecord],
    ) -> dict:
        """将分析结果更新回数据库（只更新 url_state 和 ai_abstract 字段）"""
        port = int(port)
        conn = _create_connection(
            host=host, port=port, user=user, password=password,
            database=database,
        )
        try:
            updated = 0
            failed = 0
            errors = []
            with conn.cursor() as cursor:
                for record in results:
                    pk_value = record.raw_data.get("_pk_value")
                    if pk_value is None:
                        continue

                    # 链接状态映射：中文 -> 数据库值
                    url_state = record.链接状态.value if hasattr(record.链接状态, 'value') else str(record.链接状态)
                    ai_abstract = record.AI摘要 or ""

                    try:
                        sql = f"UPDATE `{table}` SET `url_state` = %s, `ai_abstract` = %s WHERE `{pk_column}` = %s"
                        cursor.execute(sql, (url_state, ai_abstract, pk_value))
                        updated += cursor.rowcount
                    except Exception as e:
                        failed += 1
                        if len(errors) < 5:
                            errors.append(f"主键{pk_column}={pk_value}: {e}")

            conn.commit()
            result = {"success": True, "updated": updated, "failed": failed}
            if errors:
                result["errors"] = errors
            return result
        except Exception as e:
            try:
                conn.rollback()
            except Exception:
                pass
            return {"success": False, "message": str(e), "updated": 0}
        finally:
            conn.close()
