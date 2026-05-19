"""FastAPI服务端 - 招中标链接分析器Web应用"""

import os
import sys
import json
import uuid
import asyncio
import threading
import logging
from datetime import datetime
from typing import Optional
from contextlib import asynccontextmanager

# 将项目根目录加入路径
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from fastapi import FastAPI, UploadFile, File, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from bidding_url_analyzer.models import BiddingRecord, LinkStatus, AnalysisProgress
from bidding_url_analyzer.input_adapters.excel_adapter import ExcelAdapter
from bidding_url_analyzer.input_adapters.mysql_adapter import MySQLAdapter
from bidding_url_analyzer.output_writers.excel_writer import ExcelWriter
from bidding_url_analyzer.analyzer import BiddingAnalyzer


# ============ 全局状态 ============

UPLOAD_DIR = os.path.join(PROJECT_ROOT, "uploads")
RESULT_DIR = os.path.join(PROJECT_ROOT, "results")
FRONTEND_DIR = os.path.join(PROJECT_ROOT, "frontend")

os.makedirs(UPLOAD_DIR, exist_ok=True)
os.makedirs(RESULT_DIR, exist_ok=True)

# 任务存储（线程安全）
tasks: dict = {}
tasks_lock = threading.Lock()

# 操作日志存储
op_logs: list = []
op_logs_lock = threading.Lock()

# 配置日志
LOG_DIR = os.path.join(PROJECT_ROOT, "logs")
os.makedirs(LOG_DIR, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(os.path.join(LOG_DIR, "app.log"), encoding="utf-8"),
        logging.StreamHandler(),
    ],
)
logger = logging.getLogger("bidding_analyzer")


def add_op_log(action: str, detail: str = "", level: str = "info"):
    """添加操作日志"""
    log_entry = {
        "id": str(uuid.uuid4())[:8],
        "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "action": action,
        "detail": detail,
        "level": level,
    }
    with op_logs_lock:
        op_logs.append(log_entry)
        # 保留最近500条
        if len(op_logs) > 500:
            op_logs.pop(0)
    # 同时写入Python日志
    log_msg = f"{action}" + (f" - {detail}" if detail else "")
    if level == "error":
        logger.error(log_msg)
    elif level == "warning":
        logger.warning(log_msg)
    else:
        logger.info(log_msg)


# ============ 生命周期 ============

@asynccontextmanager
async def lifespan(app: FastAPI):
    yield


app = FastAPI(
    title="招中标链接分析器",
    description="检测招中标信息链接可用性并生成摘要",
    version="0.1.0",
    lifespan=lifespan,
)

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ============ API模型 ============

class TaskInfo(BaseModel):
    task_id: str
    status: str  # pending, running, completed, error
    total: int = 0
    completed: int = 0
    valid_count: int = 0
    login_count: int = 0
    invalid_count: int = 0
    filename: str = ""
    created_at: str = ""
    result_file: str = ""


class RecordResult(BaseModel):
    index: int
    标讯标题: str = ""
    原文链接: str = ""
    来源网站名称: str = ""
    招中标阶段: str = ""
    采购单位: str = ""
    中标单位: str = ""
    中标金额: float = 0
    省份: str = ""
    城市: str = ""
    链接状态: str = ""
    页面摘要: str = ""
    检测时间: str = ""
    错误信息: str = ""


# ============ 辅助函数 ============

def record_to_dict(record: BiddingRecord) -> dict:
    """将BiddingRecord转换为前端友好的字典"""
    return {
        "index": record.index,
        "标讯标题": record.标讯标题,
        "原文链接": record.原文链接,
        "来源网站名称": record.来源网站名称,
        "招中标阶段": record.招中标阶段,
        "采购单位": record.采购单位,
        "中标单位": record.中标单位,
        "中标金额": record.中标金额,
        "省份": record.省份,
        "城市": record.城市,
        "项目名称": record.项目名称,
        "项目大类": record.项目大类,
        "招标编号": record.招标编号,
        "发布时间": record.发布时间,
        "链接状态": record.链接状态.value,
        "页面摘要": record.页面摘要,
        "AI摘要": record.AI摘要,
        "检测时间": record.检测时间,
        "错误信息": record.错误信息,
    }


async def run_analysis(
    task_id: str,
    file_path: str,
    concurrency: int = 10,
    llm_api_key: str = "",
    llm_base_url: str = "",
    llm_model: str = "",
    llm_concurrency: int = 5,
):
    """异步执行分析任务（文件来源）"""
    with tasks_lock:
        task = tasks[task_id]
        task["status"] = "running"

    try:
        # 1. 读取Excel
        adapter = ExcelAdapter()
        records = adapter.load(file_path)
        with tasks_lock:
            task["total"] = len(records)

        # 2. 创建分析器（传入LLM配置）
        analyzer = BiddingAnalyzer(
            concurrency=concurrency,
            llm_api_key=llm_api_key,
            llm_base_url=llm_base_url,
            llm_model=llm_model,
            llm_concurrency=llm_concurrency,
        )

        # 3. 注册回调（线程安全更新task）
        def on_record_done(record: BiddingRecord):
            with tasks_lock:
                task["records"].append(record_to_dict(record))
                task["completed"] = analyzer.progress.completed
                task["valid_count"] = analyzer.progress.valid_count
                task["login_count"] = analyzer.progress.login_count
                task["invalid_count"] = analyzer.progress.invalid_count
                task["total_input_tokens"] = analyzer.progress.total_input_tokens
                task["total_output_tokens"] = analyzer.progress.total_output_tokens

        analyzer.on_record_done(on_record_done)

        # 4. 执行分析
        results = await analyzer.analyze(records)

        # 5. 写入结果Excel
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        result_filename = f"result_{task_id[:8]}_{timestamp}.xlsx"
        result_path = os.path.join(RESULT_DIR, result_filename)

        writer = ExcelWriter()
        writer.write(results, result_path)

        with tasks_lock:
            task["status"] = "completed"
            task["result_file"] = result_filename

    except Exception as e:
        with tasks_lock:
            task["status"] = "error"
            task["error"] = str(e)


async def run_analysis_db(
    task_id: str,
    db_config: dict,
    concurrency: int = 10,
    llm_api_key: str = "",
    llm_base_url: str = "",
    llm_model: str = "",
    llm_concurrency: int = 5,
):
    """异步执行分析任务（数据库来源）"""
    with tasks_lock:
        task = tasks[task_id]
        task["status"] = "running"

    try:
        # 1. 从数据库读取数据
        adapter = MySQLAdapter(
            host=db_config["host"],
            port=db_config["port"],
            user=db_config["user"],
            password=db_config["password"],
            database=db_config["database"],
        )
        import json
        source = json.dumps({
            "table": db_config["table"],
            "url_column": db_config["url_column"],
            "pk_column": db_config["pk_column"],
        })
        records = adapter.load(source)
        with tasks_lock:
            task["total"] = len(records)

        # 2. 创建分析器
        analyzer = BiddingAnalyzer(
            concurrency=concurrency,
            llm_api_key=llm_api_key,
            llm_base_url=llm_base_url,
            llm_model=llm_model,
            llm_concurrency=llm_concurrency,
        )

        # 3. 注册回调
        def on_record_done(record: BiddingRecord):
            with tasks_lock:
                task["records"].append(record_to_dict(record))
                task["completed"] = analyzer.progress.completed
                task["valid_count"] = analyzer.progress.valid_count
                task["login_count"] = analyzer.progress.login_count
                task["invalid_count"] = analyzer.progress.invalid_count
                task["total_input_tokens"] = analyzer.progress.total_input_tokens
                task["total_output_tokens"] = analyzer.progress.total_output_tokens

        analyzer.on_record_done(on_record_done)

        # 4. 执行分析
        results = await analyzer.analyze(records)

        # 5. 写入结果Excel
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        result_filename = f"result_{task_id[:8]}_{timestamp}.xlsx"
        result_path = os.path.join(RESULT_DIR, result_filename)
        writer = ExcelWriter()
        writer.write(results, result_path)

        # 6. 更新结果回数据库（只更新 url_state 和 ai_abstract）
        with tasks_lock:
            task["status"] = "updating_db"
            task["db_update_progress"] = {"current": 0, "total": len(results), "status": "running"}

        add_op_log("DB更新开始", f"表={db_config['table']}, 共{len(results)}条记录")

        db_update_result = MySQLAdapter.update_results(
            host=db_config["host"],
            port=db_config["port"],
            user=db_config["user"],
            password=db_config["password"],
            database=db_config["database"],
            table=db_config["table"],
            pk_column=db_config["pk_column"],
            results=results,
        )

        with tasks_lock:
            task["status"] = "completed"
            task["result_file"] = result_filename
            task["db_update_result"] = db_update_result
            task["db_update_progress"] = {"current": db_update_result.get("updated", 0), "total": len(results), "status": "completed"}

        add_op_log("DB更新完成", f"成功{db_update_result.get('updated', 0)}条, 失败{db_update_result.get('failed', 0)}条")

    except Exception as e:
        with tasks_lock:
            task["status"] = "error"
            task["error"] = str(e)


# ============ API路由 ============

@app.post("/api/upload")
async def upload_file(file: UploadFile = File(...)):
    """上传Excel文件"""
    if not file.filename.endswith((".xlsx", ".xls")):
        raise HTTPException(status_code=400, detail="仅支持Excel文件(.xlsx/.xls)")

    # 保存文件
    task_id = str(uuid.uuid4())
    file_path = os.path.join(UPLOAD_DIR, f"{task_id}_{file.filename}")

    content = await file.read()
    with open(file_path, "wb") as f:
        f.write(content)

    # 预读取记录数
    adapter = ExcelAdapter()
    try:
        records = adapter.load(file_path)
        record_count = len(records)
    except Exception as e:
        os.remove(file_path)
        raise HTTPException(status_code=400, detail=f"文件解析失败: {str(e)}")

    # 创建任务
    tasks[task_id] = {
        "task_id": task_id,
        "status": "pending",
        "total": record_count,
        "completed": 0,
        "valid_count": 0,
        "login_count": 0,
        "invalid_count": 0,
        "records": [],
        "filename": file.filename,
        "file_path": file_path,
        "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "result_file": "",
        "error": "",
    }

    add_op_log("文件上传", f"文件={file.filename}, 记录数={record_count}")

    return {
        "task_id": task_id,
        "filename": file.filename,
        "total": record_count,
    }


class AnalyzeRequest(BaseModel):
    """分析请求参数"""
    concurrency: int = 10
    llm_api_key: str = ""
    llm_base_url: str = ""
    llm_model: str = ""
    llm_concurrency: int = 5


# ============ 数据库API模型 ============

class DBConnectionInfo(BaseModel):
    """数据库连接信息"""
    host: str = "localhost"
    port: int = 3306
    user: str = "root"
    password: str = ""


class DBSelectInfo(BaseModel):
    """数据库+表选择信息"""
    host: str = "localhost"
    port: int = 3306
    user: str = "root"
    password: str = ""
    database: str = ""


class DBTableInfo(BaseModel):
    """表详细信息"""
    host: str = "localhost"
    port: int = 3306
    user: str = "root"
    password: str = ""
    database: str = ""
    table: str = ""


class DBUploadRequest(BaseModel):
    """从数据库创建任务请求"""
    host: str = "localhost"
    port: int = 3306
    user: str = "root"
    password: str = ""
    database: str = ""
    table: str = ""
    url_column: str = "原文链接"
    pk_column: str = "id"


@app.post("/api/analyze/{task_id}")
async def start_analysis(task_id: str, req: AnalyzeRequest = None):
    """启动分析任务"""
    if task_id not in tasks:
        raise HTTPException(status_code=404, detail="任务不存在")

    task = tasks[task_id]
    if task["status"] == "running":
        raise HTTPException(status_code=400, detail="任务正在运行中")

    # 解析参数
    if req is None:
        req = AnalyzeRequest()

    # 重置任务状态
    task["status"] = "pending"
    task["completed"] = 0
    task["valid_count"] = 0
    task["login_count"] = 0
    task["invalid_count"] = 0
    task["records"] = []
    task["error"] = ""

    # 启动异步分析
    add_op_log("启动分析", f"任务={task_id[:8]}, 文件来源")
    asyncio.create_task(run_analysis(
        task_id,
        task["file_path"],
        concurrency=req.concurrency,
        llm_api_key=req.llm_api_key,
        llm_base_url=req.llm_base_url,
        llm_model=req.llm_model,
        llm_concurrency=req.llm_concurrency,
    ))

    return {"task_id": task_id, "status": "started"}


# ============ 数据库API路由 ============

@app.post("/api/db/test")
async def db_test_connection(info: DBConnectionInfo):
    """测试数据库连接"""
    result = MySQLAdapter.test_connection(
        host=info.host, port=info.port, user=info.user, password=info.password,
    )
    if not result["success"]:
        add_op_log("DB连接测试", f"失败: host={info.host}:{info.port}", "error")
        raise HTTPException(status_code=400, detail=result["message"])
    add_op_log("DB连接测试", f"成功: host={info.host}:{info.port}")
    return result


@app.post("/api/db/databases")
async def db_list_databases(info: DBConnectionInfo):
    """列出所有数据库"""
    try:
        databases = MySQLAdapter.list_databases(
            host=info.host, port=info.port, user=info.user, password=info.password,
        )
        return {"databases": databases}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/api/db/tables")
async def db_list_tables(info: DBSelectInfo):
    """列出指定数据库的所有表"""
    try:
        tables = MySQLAdapter.list_tables(
            host=info.host, port=info.port, user=info.user,
            password=info.password, database=info.database,
        )
        return {"tables": tables}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/api/db/columns")
async def db_list_columns(info: DBTableInfo):
    """列出指定表的所有列"""
    try:
        columns = MySQLAdapter.list_columns(
            host=info.host, port=info.port, user=info.user,
            password=info.password, database=info.database, table=info.table,
        )
        return {"columns": columns}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/api/db/preview")
async def db_preview_data(info: DBTableInfo):
    """预览表数据"""
    try:
        rows = MySQLAdapter.preview_data(
            host=info.host, port=info.port, user=info.user,
            password=info.password, database=info.database, table=info.table,
            limit=10,
        )
        # 将非字符串值转为字符串以便JSON序列化
        for row in rows:
            for key in row:
                if row[key] is not None:
                    row[key] = str(row[key])
                else:
                    row[key] = ""
        return {"rows": rows, "count": len(rows)}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/api/db/upload")
async def db_upload(info: DBUploadRequest):
    """从数据库创建分析任务"""
    try:
        # 验证连接和数据
        adapter = MySQLAdapter(
            host=info.host, port=info.port, user=info.user,
            password=info.password, database=info.database,
        )
        import json
        source = json.dumps({
            "table": info.table,
            "url_column": info.url_column,
            "pk_column": info.pk_column,
        })
        record_count = adapter.get_record_count(source)

        if record_count == 0:
            raise HTTPException(status_code=400, detail="表中没有数据")

        # 创建任务
        task_id = str(uuid.uuid4())
        tasks[task_id] = {
            "task_id": task_id,
            "status": "pending",
            "total": record_count,
            "completed": 0,
            "valid_count": 0,
            "login_count": 0,
            "invalid_count": 0,
            "records": [],
            "filename": f"{info.database}.{info.table}",
            "file_path": "",
            "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "result_file": "",
            "error": "",
            "source_type": "database",
            "db_config": {
                "host": info.host,
                "port": info.port,
                "user": info.user,
                "password": info.password,
                "database": info.database,
                "table": info.table,
                "url_column": info.url_column,
                "pk_column": info.pk_column,
            },
        }

        return {
            "task_id": task_id,
            "filename": f"{info.database}.{info.table}",
            "total": record_count,
        }
    except HTTPException:
        raise
    except Exception as e:
        add_op_log("DB创建任务失败", f"{info.database}.{info.table}: {e}", "error")
        raise HTTPException(status_code=400, detail=str(e))

    add_op_log("DB创建任务", f"{info.database}.{info.table}, {record_count}条记录")


class DBAnalyzeRequest(BaseModel):
    """数据库分析请求参数"""
    concurrency: int = 10
    llm_api_key: str = ""
    llm_base_url: str = ""
    llm_model: str = ""
    llm_concurrency: int = 5


@app.post("/api/analyze-db/{task_id}")
async def start_db_analysis(task_id: str, req: DBAnalyzeRequest = None):
    """启动数据库来源的分析任务"""
    if task_id not in tasks:
        raise HTTPException(status_code=404, detail="任务不存在")

    task = tasks[task_id]
    if task.get("source_type") != "database":
        raise HTTPException(status_code=400, detail="此任务不是数据库来源")

    if task["status"] == "running":
        raise HTTPException(status_code=400, detail="任务正在运行中")

    if req is None:
        req = DBAnalyzeRequest()

    # 重置任务状态
    task["status"] = "pending"
    task["completed"] = 0
    task["valid_count"] = 0
    task["login_count"] = 0
    task["invalid_count"] = 0
    task["records"] = []
    task["error"] = ""

    # 启动异步分析
    add_op_log("启动DB分析", f"任务={task_id[:8]}, 表={task['db_config']['table']}")
    asyncio.create_task(run_analysis_db(
        task_id,
        db_config=task["db_config"],
        concurrency=req.concurrency,
        llm_api_key=req.llm_api_key,
        llm_base_url=req.llm_base_url,
        llm_model=req.llm_model,
        llm_concurrency=req.llm_concurrency,
    ))

    return {"task_id": task_id, "status": "started"}


@app.get("/api/status/{task_id}")
async def get_status(task_id: str):
    """获取任务状态"""
    with tasks_lock:
        if task_id not in tasks:
            raise HTTPException(status_code=404, detail="任务不存在")
        task = tasks[task_id]
        return {
            "task_id": task["task_id"],
            "status": task["status"],
            "total": task["total"],
            "completed": task["completed"],
            "valid_count": task["valid_count"],
            "login_count": task["login_count"],
            "invalid_count": task["invalid_count"],
            "filename": task["filename"],
            "created_at": task["created_at"],
            "result_file": task["result_file"],
            "error": task.get("error", ""),
            "source_type": task.get("source_type", "file"),
            "db_update_result": task.get("db_update_result", None),
            "db_update_progress": task.get("db_update_progress", None),
            "total_input_tokens": task.get("total_input_tokens", 0),
            "total_output_tokens": task.get("total_output_tokens", 0),
        }


@app.get("/api/records/{task_id}")
async def get_records(
    task_id: str,
    status: Optional[str] = None,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=5000),
    search: Optional[str] = None,
):
    """获取分析结果记录"""
    with tasks_lock:
        if task_id not in tasks:
            raise HTTPException(status_code=404, detail="任务不存在")
        task = tasks[task_id]
        records = list(task["records"])

    # 按状态筛选
    if status and status != "全部":
        records = [r for r in records if r["链接状态"] == status]

    # 搜索
    if search:
        search_lower = search.lower()
        records = [
            r for r in records
            if search_lower in r.get("标讯标题", "").lower()
            or search_lower in r.get("采购单位", "").lower()
            or search_lower in r.get("中标单位", "").lower()
            or search_lower in r.get("来源网站名称", "").lower()
        ]

    # 分页
    total = len(records)
    start = (page - 1) * page_size
    end = start + page_size
    page_records = records[start:end]

    return {
        "total": total,
        "page": page,
        "page_size": page_size,
        "records": page_records,
    }


@app.get("/api/stats/{task_id}")
async def get_stats(task_id: str):
    """获取统计信息"""
    with tasks_lock:
        if task_id not in tasks:
            raise HTTPException(status_code=404, detail="任务不存在")
        task = tasks[task_id]
        records = list(task["records"])
        valid_count = task["valid_count"]
        login_count = task["login_count"]
        invalid_count = task["invalid_count"]

    # 来源网站分布
    source_dist = {}
    for r in records:
        src = r.get("来源网站名称", "未知")
        source_dist[src] = source_dist.get(src, 0) + 1

    # 链接状态分布
    status_dist = {
        "有效": valid_count,
        "需登录查看": login_count,
        "链接无效": invalid_count,
    }

    # 金额区间分布
    amount_ranges = {"0元": 0, "0-10万": 0, "10-50万": 0, "50-100万": 0, "100万以上": 0}
    for r in records:
        amt = r.get("中标金额", 0) or 0
        if amt == 0:
            amount_ranges["0元"] += 1
        elif amt < 100000:
            amount_ranges["0-10万"] += 1
        elif amt < 500000:
            amount_ranges["10-50万"] += 1
        elif amt < 1000000:
            amount_ranges["50-100万"] += 1
        else:
            amount_ranges["100万以上"] += 1

    # 地区分布
    region_dist = {}
    for r in records:
        prov = r.get("省份", "未知") or "未知"
        region_dist[prov] = region_dist.get(prov, 0) + 1

    return {
        "source_distribution": dict(sorted(source_dist.items(), key=lambda x: -x[1])[:15]),
        "status_distribution": status_dist,
        "amount_distribution": amount_ranges,
        "region_distribution": dict(sorted(region_dist.items(), key=lambda x: -x[1])[:15]),
    }


@app.get("/api/export/{task_id}")
async def export_result(task_id: str):
    """导出分析结果"""
    with tasks_lock:
        if task_id not in tasks:
            raise HTTPException(status_code=404, detail="任务不存在")
        task = tasks[task_id]
        task_status = task["status"]
        task_result_file = task["result_file"]
        task_filename = task["filename"]

    if task_status != "completed":
        raise HTTPException(status_code=400, detail="任务尚未完成")

    if not task_result_file:
        raise HTTPException(status_code=404, detail="结果文件不存在")

    result_path = os.path.join(RESULT_DIR, task_result_file)
    if not os.path.exists(result_path):
        raise HTTPException(status_code=404, detail="结果文件已丢失")

    return FileResponse(
        result_path,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        filename=f"分析结果_{task_filename}",
    )


@app.get("/api/logs")
async def get_logs(limit: int = Query(default=100, ge=1, le=500)):
    """获取操作日志"""
    with op_logs_lock:
        return {"logs": list(op_logs[-limit:]), "total": len(op_logs)}


# ============ 前端页面 ============

@app.get("/")
async def index():
    """返回前端页面"""
    html_path = os.path.join(FRONTEND_DIR, "index.html")
    if os.path.exists(html_path):
        with open(html_path, "r", encoding="utf-8") as f:
            return HTMLResponse(content=f.read())
    return HTMLResponse(content="<h1>前端页面未找到</h1>", status_code=404)
