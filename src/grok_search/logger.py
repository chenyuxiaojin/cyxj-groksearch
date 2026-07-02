import logging
from datetime import datetime
from pathlib import Path
from .config import config

logger = logging.getLogger("grok_search")
logger.setLevel(getattr(logging, config.log_level, logging.INFO))

_formatter = logging.Formatter(
    '%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)

try:
    log_dir = config.log_dir
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / f"grok_search_{datetime.now().strftime('%Y%m%d')}.log"

    file_handler = logging.FileHandler(log_file, encoding='utf-8')
    file_handler.setLevel(getattr(logging, config.log_level, logging.INFO))
    file_handler.setFormatter(_formatter)
    logger.addHandler(file_handler)
except OSError:
    logger.addHandler(logging.NullHandler())

async def log_info(ctx, message: str, is_debug: bool = False):
    """仅在 debug 开启时写文件日志并向客户端推送 MCP 通知。

    非 debug 路径零开销：之前无论是否 debug 都会对每条消息发一次 ctx.info
    通知（包括整段搜索结果正文），白白增加每次调用的往返和序列化成本。"""
    if not is_debug:
        return
    logger.info(message)
    if ctx:
        await ctx.info(message)
