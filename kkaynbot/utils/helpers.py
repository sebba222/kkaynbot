import time
import logging
import requests

logger = logging.getLogger(__name__)

def sf(v):
    try: return float(str(v).replace(",", ".")) if v else 0.0
    except: return 0.0

def bal(data, cuenta):
    b = 0.0
    for r in data[3:]:
        if len(r) >= 7 and r[3] == cuenta:
            b += sf(r[5]) - sf(r[6])
    return b

def usd_rate():
    try:
        return requests.get("https://api.exchangerate-api.com/v4/latest/USD", timeout=5).json()["rates"].get("UYU", 40.0)
    except:
        return 40.0

def with_retry(func, *args, max_retries=3, **kwargs):
    """Ejecuta una función con retry automático ante 429."""
    for attempt in range(max_retries):
        try:
            return func(*args, **kwargs)
        except Exception as e:
            err = str(e)
            if "429" in err or "RESOURCE_EXHAUSTED" in err or "RATE_LIMIT" in err:
                wait = 15 * (attempt + 1)
                logger.warning(f"Rate limit (intento {attempt+1}), esperando {wait}s...")
                time.sleep(wait)
                if attempt == max_retries - 1:
                    raise
            else:
                raise
    return None
