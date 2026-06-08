CMAP = {
    "bbva uyu": "BBVA UYU", "bbva usd": "BBVA USD",
    "itau uyu": "Itaú UYU", "itaú uyu": "Itaú UYU", "itaù uyu": "Itaú UYU", "itàu uyu": "Itaú UYU",
    "itau usd": "Itaú USD", "itaú usd": "Itaú USD", "itaù usd": "Itaú USD", "itàu usd": "Itaú USD",
    "efectivo uyu": "Efectivo UYU", "efectivo usd": "Efectivo USD",
}

def nc(n):
    return CMAP.get((n or "").lower().strip(), n)
