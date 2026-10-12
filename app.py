"""SmartCart Solutions Ltd - Flask web application."""
from flask import Flask, abort, render_template, request, send_file, send_from_directory

import config
import currency
import database
import exporter
import scraper

app = Flask(__name__)


def add_commission(products, rate):
    """Convert USD -> RWF, then Commission_fee = 10% of price; Total = price + commission."""
    for p in products:
        usd = p.pop("product_price")
        p["price_usd"] = round(usd, 2)
        p["commission_usd"] = round(usd * config.COMMISSION_RATE, 2)
        p["total_usd"] = round(p["price_usd"] + p["commission_usd"], 2)

        p["product_price"] = round(usd * rate, 2)                              # RWF
        p["commission_fee"] = round(p["product_price"] * config.COMMISSION_RATE, 2)
        p["total_price"] = round(p["product_price"] + p["commission_fee"], 2)
        p["exchange_rate"] = rate
    return products


def run_search(term, force_refresh=False):
    """Cache check -> (live search) -> logging. Returns (products, meta).
    meta["source"] is "cache", "live" or "sample" (labelled example data)."""
    products = None if force_refresh else database.get_cached(term)

    if products is None:
        rate, rate_source = currency.get_rate()
        try:
            products, source = scraper.search_amazon(term), "live"
        except scraper.ScraperError:
            if not getattr(config, "SAMPLE_FALLBACK", True):
                raise
            products, source = scraper.sample_products(term), "sample"
        products = add_commission(products, rate)
        products = exporter.download_pictures(products)
        database.save_cache(term, products, rate, sample=(source == "sample"))
    else:
        source = "cache"
        rate, rate_source = products[0]["exchange_rate"], "saved"
        products = exporter.download_pictures(products)   # re-download missing pictures

    database.log_search(term, len(products), source)
    if source != "sample":                                # example data is not real revenue
        database.log_commission(term, products, rate)
    return products, {"source": source, "rate": rate, "rate_source": rate_source}


@app.route("/", methods=["GET", "POST"])
def index():
    context = {"products": None, "term": "", "error": None, "meta": None,
               "excel_path": None}

    term = (request.form.get("product_name") or request.args.get("q") or "").strip()
    submitted = request.method == "POST" or "q" in request.args

    if submitted:
        context["term"] = term
        if not term:
            context["error"] = "Please enter a product name."
        elif len(term) > 100:
            context["error"] = "The product name is too long (max 100 characters)."
        else:
            try:
                products, meta = run_search(term, force_refresh="refresh" in request.values)
                context["products"], context["meta"] = products, meta
                context["excel_path"] = str(exporter.save_excel(products, term, meta["rate"]))
            except scraper.ScraperError as exc:
                context["error"] = str(exc)
            except database.DatabaseError as exc:
                context["error"] = f"{exc}. Is MongoDB running?"
            except PermissionError as exc:
                context["error"] = str(exc)
            except Exception as exc:                         # last-resort safety net
                context["error"] = f"Unexpected error: {exc}"

    if context["meta"] is None:
        context["meta"] = {"rate": currency.get_rate()[0]}
    context["rate_source"] = currency.get_rate()[1]
    context["history"] = database.recent_searches()
    context["stats"] = database.get_stats()
    return render_template("index.html", **context)


@app.route("/download/<fmt>")
def download(fmt):
    """Browser download of the latest results for ?q=<term> (Excel or CSV)."""
    term = request.args.get("q", "").strip()
    products = database.get_cached(term, ignore_age=True) if term else None
    if not products:
        abort(404, "Search for a product first, then download its results.")
    rate = products[0]["exchange_rate"]
    if fmt == "excel":
        return send_file(exporter.excel_bytes(products, term, rate), as_attachment=True,
                         download_name=config.EXCEL_FILENAME,
                         mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    if fmt == "csv":
        return send_file(exporter.csv_bytes(products), as_attachment=True,
                         download_name=config.EXCEL_FILENAME.replace(".xlsx", ".csv"),
                         mimetype="text/csv")
    abort(404)


@app.route("/pictures/<path:filename>")
def pictures(filename):
    return send_from_directory(config.PICTURES_DIR, filename)


if __name__ == "__main__":
    app.run(debug=True)