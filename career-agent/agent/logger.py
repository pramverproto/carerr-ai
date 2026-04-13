import logging
import os
from datetime import datetime

# 日志目录：项目根目录下的 logs/
_LOG_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "logs")


def get_logger(name: str = "agent") -> logging.Logger:
    """
    获取统一配置的 logger。
    - 控制台：INFO 及以上，方便实时查看对话流程。
    - 文件：DEBUG 及以上，记录完整 trace（工具调用、LLM 耗时等）。
    - 每次启动生成新的日志文件，按启动时间命名，避免单文件过大。
    同一 name 多次调用只初始化一次（logging 模块内部有缓存）。
    """
    logger = logging.getLogger(name)

    # 已经初始化过，直接返回（防止重复添加 handler）
    if logger.handlers:
        return logger

    logger.setLevel(logging.DEBUG)

    fmt = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s - %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # 控制台 handler：INFO+
    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.INFO)
    console_handler.setFormatter(fmt)

    # 文件 handler：DEBUG+，每次启动新文件
    os.makedirs(_LOG_DIR, exist_ok=True)
    log_file = os.path.join(_LOG_DIR, f"{datetime.now().strftime('%Y%m%d_%H%M%S')}.log")
    file_handler = logging.FileHandler(log_file, encoding="utf-8")
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(fmt)

    logger.addHandler(console_handler)
    logger.addHandler(file_handler)

    return logger
