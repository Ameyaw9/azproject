"""AI company market-cap comparison.

Run:  flask --app app run --debug     then open http://127.0.0.1:5000
"""
import math
import re
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import quote_plus

import pandas as pd
import requests
from flask import Flask, jsonify, render_template, request

app = Flask(__name__)

DATA_DATE = "1 October 2026"

# ---------------------------------------------------------------- data
df = pd.read_csv(Path(__file__).parent / "data" / "companies.csv").rename(
    columns={
        "Rank": "rank",
        "Name": "name",
        "Symbol": "symbol",
        "marketcap": "market_cap",
        "price (USD)": "price",
        "country": "country",
    }
)
COMPANIES = df.to_dict("records")
BY_SYMBOL = {c["symbol"].upper(): c for c in COMPANIES}
TOTAL_CAP = float(df["market_cap"].sum())
COUNT = len(df)
MAX_CAP = float(df["market_cap"].max())
R_MAX = 115  # radius (px) of the biggest circle in the comparison graphic

DEFAULT_A, DEFAULT_B = "NVDA", "CRWV"


# ------------------------------------------------------------ formatting
def money(n):
    for limit, suffix in ((1e12, "T"), (1e9, "B"), (1e6, "M"), (1e3, "K")):
        if n >= limit:
            v = n / limit
            return f"${v:,.2f}{suffix}" if v < 10 else f"${v:,.1f}{suffix}"
    return f"${n:,.0f}"


def price_text(p):
    return f"${p:,.4f}" if p < 1 else f"${p:,.2f}"


def shares_text(n):
    for limit, suffix in ((1e9, "B"), (1e6, "M"), (1e3, "K")):
        if n >= limit:
            return f"{n / limit:,.2f}{suffix}"
    return f"{n:,.0f}"


def tier(cap):
    if cap >= 200e9:
        return "mega cap"
    if cap >= 10e9:
        return "large cap"
    if cap >= 1e9:
        return "mid cap"
    if cap >= 100e6:
        return "small cap"
    return "micro cap"


def ratio_text(r):
    return f"{r:,.0f}×" if r >= 10 else f"{r:.1f}×"


# -------------------------------------------------------------- analysis
def profile(c):
    cap = float(c["market_cap"])
    return {
        "name": c["name"],
        "symbol": c["symbol"],
        "rank": int(c["rank"]),
        "country": c["country"],
        "market_cap": cap,
        "cap_text": money(cap),
        "share_pct": cap / TOTAL_CAP * 100,
        "tier": tier(cap),
        "price_text": price_text(float(c["price"])),
        "shares_text": shares_text(cap / float(c["price"])),
        "radius": max(2.5, R_MAX * math.sqrt(cap / MAX_CAP)),
        "google_url": "https://www.google.com/search?q="
        + quote_plus(f"{c['name']} stock market cap"),
    }


def compare(a, b):
    """Build the side-by-side analysis for two companies."""
    pa, pb = profile(a), profile(b)
    big, small = (pa, pb) if pa["market_cap"] >= pb["market_cap"] else (pb, pa)
    ratio = big["market_cap"] / small["market_cap"]
    gap = abs(big["rank"] - small["rank"])
    place_word = "place" if gap == 1 else "places"

    insights = [
        f"{big['name']} is about {ratio_text(ratio)} the size of {small['name']} by market cap.",
        f"Together they make up {pa['share_pct'] + pb['share_pct']:.1f}% of the "
        f"{COUNT} companies' combined {money(TOTAL_CAP)}.",
        f"{big['name']} ranks #{big['rank']} and {small['name']} ranks #{small['rank']}, "
        f"{gap} {place_word} apart.",
    ]
    if pa["tier"] == pb["tier"]:
        insights.append(f"Both are {pa['tier']} companies.")
    else:
        insights.append(
            f"They sit in different size tiers: {pa['name']} is {pa['tier']}, "
            f"{pb['name']} is {pb['tier']}."
        )
    insights.append(
        f"Both are based in {pa['country']}."
        if pa["country"] == pb["country"]
        else f"{pa['name']} is based in {pa['country']}; {pb['name']} in {pb['country']}."
    )

    return {
        "a": pa,
        "b": pb,
        "insights": insights,
        "google_url": "https://www.google.com/search?q="
        + quote_plus(f"{pa['name']} vs {pb['name']}"),
    }


# ------------------------------------------------- short descriptions
WIKI_API = "https://en.wikipedia.org/w/api.php"
_blurb_cache = {}  # only successful lookups are cached


def fetch_blurb(name):
    """Two-sentence summary from Wikipedia, or None if unavailable."""
    if name in _blurb_cache:
        return _blurb_cache[name]
    query = re.sub(r"\s*\(.*?\)", "", name).strip()  # "Alphabet (Google)" -> "Alphabet"
    params = {
        "action": "query",
        "format": "json",
        "generator": "search",
        "gsrsearch": f"{query} company",
        "gsrlimit": 1,
        "prop": "extracts",
        "exintro": 1,
        "explaintext": 1,
        "exsentences": 2,
        "redirects": 1,
    }
    try:
        r = requests.get(
            WIKI_API,
            params=params,
            timeout=4,
            headers={"User-Agent": "ai-market-compare/0.1 (learning project)"},
        )
        r.raise_for_status()
        pages = r.json().get("query", {}).get("pages", {})
        page = next(iter(pages.values()), None)
        if page and page.get("extract"):
            blurb = {
                "text": page["extract"].strip(),
                "title": page["title"],
                "url": f"https://en.wikipedia.org/?curid={page['pageid']}",
            }
            _blurb_cache[name] = blurb
            return blurb
    except (requests.RequestException, ValueError, KeyError):
        pass
    return None


# ---------------------------------------------------------------- routes
def resolve(symbol):
    return BY_SYMBOL.get((symbol or "").strip().upper())


@app.route("/")
def index():
    sym_a = request.args.get("a", DEFAULT_A)
    sym_b = request.args.get("b", DEFAULT_B)
    a, b = resolve(sym_a), resolve(sym_b)

    result, error, blurbs = None, None, (None, None)
    if not a or not b:
        error = "Pick two companies from the lists."
    elif a["symbol"] == b["symbol"]:
        error = "Pick two different companies."
    else:
        result = compare(a, b)
        with ThreadPoolExecutor(max_workers=2) as pool:
            blurbs = tuple(pool.map(fetch_blurb, (a["name"], b["name"])))

    return render_template(
        "index.html",
        companies=COMPANIES,
        sel_a=sym_a.upper(),
        sel_b=sym_b.upper(),
        result=result,
        blurbs=blurbs,
        error=error,
        data_date=DATA_DATE,
    )


@app.route("/api/compare")
def api_compare():
    a, b = resolve(request.args.get("a")), resolve(request.args.get("b"))
    if not a or not b or a["symbol"] == b["symbol"]:
        return jsonify(error="Provide two different valid symbols as ?a=NVDA&b=AAPL"), 400
    return jsonify(compare(a, b))


if __name__ == "__main__":
    app.run(debug=True)
