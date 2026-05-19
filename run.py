"""招中标链接分析器 - 启动入口"""

import os
import sys
import webbrowser
import argparse

# 将项目根目录加入路径
PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)


def main():
    parser = argparse.ArgumentParser(description="招中标链接分析器")
    parser.add_argument("--host", default="127.0.0.1", help="服务地址 (默认: 127.0.0.1)")
    parser.add_argument("--port", type=int, default=8765, help="服务端口 (默认: 8765)")
    parser.add_argument("--no-browser", action="store_true", help="不自动打开浏览器")
    args = parser.parse_args()

    import uvicorn

    if not args.no_browser:
        url = f"http://{args.host}:{args.port}"
        import threading
        def open_browser():
            import time
            time.sleep(1.5)
            webbrowser.open(url)
        threading.Thread(target=open_browser, daemon=True).start()

    print(f"\n{'='*50}")
    print(f"  招中标链接分析器 已启动")
    print(f"  访问地址: http://{args.host}:{args.port}")
    print(f"  按 Ctrl+C 停止服务")
    print(f"{'='*50}\n")

    uvicorn.run(
        "server.main:app",
        host=args.host,
        port=args.port,
        reload=False,
        log_level="info",
    )


if __name__ == "__main__":
    main()
