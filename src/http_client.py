"""HTTP-запросы с повторными попытками: таймауты, 429 (rate limit), 5xx, обрывы соединения."""
import logging
import time

import requests

log = logging.getLogger(__name__)


class SourceUnavailable(Exception):
    """Источник не ответил после всех попыток."""


def get(url: str, *, params: dict | None = None, timeout: int = 60, retries: int = 5,
        backoff: float = 2.0, stream: bool = False) -> requests.Response:
    for attempt in range(1, retries + 1):
        wait = backoff ** attempt
        try:
            resp = requests.get(url, params=params, timeout=timeout, stream=stream)
        except (requests.ConnectionError, requests.Timeout) as exc:
            log.warning("%s: попытка %d/%d, сетевая ошибка: %s", url, attempt, retries, exc)
        else:
            if resp.status_code == 429:
                wait = float(resp.headers.get("Retry-After", wait))
                log.warning("%s: 429 Too Many Requests, ждём %.0f с", url, wait)
            elif resp.status_code >= 500:
                log.warning("%s: попытка %d/%d, HTTP %d", url, attempt, retries, resp.status_code)
            elif resp.status_code >= 400:
                raise SourceUnavailable(f"{url}: HTTP {resp.status_code}")
            else:
                return resp
        if attempt < retries:
            time.sleep(wait)
    raise SourceUnavailable(f"{url}: нет ответа после {retries} попыток")
