"""Central settings for the SmartCart application. Edit the student details below."""
import os
from pathlib import Path

# ---- Student details (used for the Excel file name) ----
FIRST_NAME = "Vallana"
LAST_NAME = "YourLastName"        # <-- change this to your real last name
REGISTRATION_NUMBER = "248RP1174"

# ---- Business rules ----
COMMISSION_RATE = 0.10            # 10% of the product price
CACHE_MINUTES = 60                # how long a cached search stays "recent"
MAX_RESULTS = 60                  # products kept per search
SEARCH_PAGES = 3                  # how many Amazon result pages to read per search

# ---- Currency (Amazon prices are in USD, we show Rwandan francs) ----
RATE_API_URL = "https://open.er-api.com/v6/latest/USD"   # free, no key needed
FALLBACK_USD_TO_RWF = 1450.0       # used only if the live rate cannot be fetched
RATE_REFRESH_HOURS = 6

# ---- MongoDB ----
MONGO_URI = os.environ.get("MONGO_URI", "mongodb://localhost:27017")
DB_NAME = "smartcart"

# ---- Folders ----
BASE_DIR = Path(__file__).resolve().parent
PICTURES_DIR = BASE_DIR / "Online_Pictures"


def get_desktop_path() -> Path:
    """Return the user's Desktop (handles OneDrive-redirected desktops on Windows)."""
    candidates = [
        Path.home() / "Desktop",
        Path.home() / "OneDrive" / "Desktop",
    ]
    for path in candidates:
        if path.exists():
            return path
    return candidates[0]


EXCEL_FILENAME = f"{FIRST_NAME}_{LAST_NAME}_{REGISTRATION_NUMBER}.xlsx"
