# 招中标链接分析器 (Bidding URL Analyzer)

检测招中标信息链接可用性，自动生成结构化摘要。

## 功能特性

- **链接状态三分类**：有效 / 需登录查看 / 链接无效
- **结构化摘要提取**：自动提取项目名称、招标编号、招标业主、中标单位、金额等
- **实时进度可视化**：Web界面实时展示分析进度和结果
- **统计分析**：来源网站分布、链接状态分布、金额区间分布、地区分布
- **结果导出**：支持导出为 Excel 文件

## 快速开始

### 1. 安装依赖

```bash
cd bidding-url-analyzer
pip install -r requirements.txt
```

### 2. 启动服务

```bash
python run.py
```

服务启动后会自动打开浏览器，访问 `http://127.0.0.1:8765`

### 3. 使用步骤

1. 在Web界面上传 Excel 文件（包含招中标数据，必须有 `原文链接` 列）
2. 点击"开始分析"
3. 实时查看分析进度和结果
4. 点击"查看"可查看每条记录的摘要
5. 分析完成后可查看统计图表
6. 点击"导出Excel"下载分析结果

## 项目结构

```
bidding-url-analyzer/
├── bidding_url_analyzer/        # 核心Python包
│   ├── models.py               # 数据模型
│   ├── analyzer.py             # 核心编排器
│   ├── fetcher/
│   │   ├── httpx_fetcher.py    # 轻量HTTP抓取器
│   │   └── playwright_fetcher.py # 重型浏览器抓取器
│   ├── extractor/
│   │   └── summary_extractor.py # 摘要提取器
│   ├── input_adapters/
│   │   ├── base.py             # 输入适配器基类
│   │   └── excel_adapter.py    # Excel适配器
│   └── output_writers/
│       ├── base.py             # 输出写入器基类
│       └── excel_writer.py     # Excel写入器
├── server/
│   └── main.py                 # FastAPI服务端
├── frontend/
│   └── index.html              # Vue 3前端界面
├── run.py                      # 启动入口
└── requirements.txt            # Python依赖
```

## 扩展说明

- **新增输入源**：继承 `InputAdapter` 基类，实现 `load()` 方法
- **新增输出格式**：继承 `OutputWriter` 基类，实现 `write()` 方法
- **启用Playwright**：安装 `playwright` 并运行 `playwright install chromium`，用于JS渲染页面
