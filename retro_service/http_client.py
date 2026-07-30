from requests import Session
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry


def make_session(pool_size: int = 32) -> Session:
    """创建可复用 HTTP Session，并对瞬时 5xx 做少量重试。"""
    session = Session()
    retry = Retry(
        total=1,
        connect=1,
        read=0,
        status=1,
        backoff_factor=0.2,
        status_forcelist=(500, 502, 503, 504),
        allowed_methods=frozenset(["GET", "POST"]),
    )
    adapter = HTTPAdapter(max_retries=retry, pool_connections=pool_size, pool_maxsize=pool_size)
    session.mount("http://", adapter)
    session.mount("https://", adapter)
    return session
