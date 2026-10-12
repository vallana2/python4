"""Run:  python diagnose.py "usb cable"  - tests a real search and shows what happens."""
import sys
import time
import scraper

term = " ".join(sys.argv[1:]) or "usb cable"
start = time.time()
try:
    products = scraper.search_amazon(term)
    print(f"\nSUCCESS: {len(products)} products with prices in {time.time() - start:.0f}s")
    for p in products[:5]:
        print("  -", p["product_name"][:60], "| $", p["product_price"], "| rating", p["global_rating"])
except scraper.ScraperError as exc:
    print("\nFAILED:", exc)
    print("The last page Amazon sent was saved to", scraper.DEBUG_FILE)