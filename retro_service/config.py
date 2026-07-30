import logging
import os


logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

# 本地逆合成候选接口
LOCAL_RETRO_API_URL = os.getenv("RETRO_API_URL", "http://192.168.1.144:5000/retro_search")

# 价格/库存接口：SciFinder 路线与本地路线都统一使用这套价格来源
PRICE_DETAIL_URL = os.getenv("PRICE_API_BASE", "https://api.so.aiphacas.com/api/detail")
PRICE_QUERY_URL = os.getenv("PRICE_QUERY_URL", "https://price.aiphacas.com/api/price/query")
PRICE_DEFAULT_UNIT = os.getenv("PRICE_DEFAULT_UNIT", "CNY")

# SciFinder 远程路线接口
SCIFINDER_RETRO_API_URL = os.getenv("SCIFINDER_RETRO_API_URL", "https://retro.aiphacas.com/retro")
SCIFINDER_API_KEY = "j4wwrww5Uy28aKcPQofKsGmX6EbOFRF7pgCI2VJb9-s"

DEFAULT_API_TIMEOUT = float(os.getenv("RETRO_API_TIMEOUT", "120"))
DEFAULT_PRICE_TIMEOUT = float(os.getenv("PRICE_API_TIMEOUT", "10"))
DEFAULT_TIME_BUDGET = float(os.getenv("PLAN_TIME_BUDGET", "600"))
DEFAULT_CACHE_SIZE = int(os.getenv("PLAN_CACHE_SIZE", "5000"))
DEFAULT_SCIFINDER_TIMEOUT = float(os.getenv("SCIFINDER_TIMEOUT", "600"))

APP_TITLE = "RetroSynthesis API"
APP_DESCRIPTION = "AI 逆合成路径规划服务 API"
APP_VERSION = "1.2-refactored"
