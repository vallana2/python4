"""USD -> RWF exchange rate (live from a free API, with a safe fallback)."""
import time
import requests

import config

_state = {"rate": None, "fetched": 0.0, "source": "fallback"}


def get_rate():
    """Return (rate, source) where source is 'live' or 'fallback'."""
    now = time.time()
    if _state["rate"] and now - _state["fetched"] < config.RATE_REFRESH_HOURS * 3600:
        return _state["rate"], _state["source"]
    try:
        response = requests.get(config.RATE_API_URL, timeout=5)
        response.raise_for_status()
        _state.update(rate=float(response.json()["rates"]["RWF"]),
                      fetched=now, source="live")
    except Exception:
        # Keep the last known rate, or the fallback. Retry in ~30 minutes.
        _state.update(rate=_state["rate"] or config.FALLBACK_USD_TO_RWF,
                      fetched=now - (config.RATE_REFRESH_HOURS * 3600 - 1800),
                      source="fallback")
    return _state["rate"], _state["source"]
