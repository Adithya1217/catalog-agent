"""
seed.py -- one-time script (Phase 2).

Seeds 15 messy raw SKUs for Time & Co. + 1 mandate. Run once, then
ignore. Deliberately keep the raw data messy (inconsistent naming,
missing descriptions, inconsistent units) -- that's the raw material
enrich.py needs to visibly prove it's doing real work.

Time & Co. is a stationery and office-supplies shop in Chennai, run
out of a single storefront near T. Nagar for the last dozen years,
known locally for stocking odd/bulk sizes bigger chains don't bother
with. Listings have never been touched by anyone but the owner, typed
up over years in whatever format he was in a hurry with.
"""

from models import CatalogItem, Mandate, SessionLocal, init_db

RAW_SKUS: list[dict] = [
    {
        "raw_name": "Blue Ball Pen (Pack of 10)",
        "raw_description": "Smooth writing, medium tip",
        "price_raw": "₹45",
        "stock": 120,
        "terms": "min order 1 pack",
    },
    {
        "raw_name": "A4 Notebook 200pg Ruled",
        "raw_description": None,
        "price_raw": "Rs. 85/-",
        "stock": 60,
        "terms": None,
    },
    {
        "raw_name": "sticky notes asstd colors",
        "raw_description": "3x3 inch, 100 sheets/pad",
        "price_raw": "30",
        "stock": 200,
        "terms": "pack of 4 pads",
    },
    {
        "raw_name": "STAPLER SMALL NO.10",
        "raw_description": "Includes 1 box pins",
        "price_raw": "₹60",
        "stock": 45,
        "terms": None,
    },
    {
        "raw_name": "Whiteboard Markers (set of 4)",
        "raw_description": "black blue red green, low odour",
        "price_raw": "Rs.120/-",
        "stock": 35,
        "terms": None,
    },
    {
        "raw_name": "gel pen 0.5mm blk",
        "raw_description": None,
        "price_raw": "15",
        "stock": 300,
        "terms": "sold per piece",
    },
    {
        "raw_name": "File Folder A4",
        "raw_description": "plastic, transparent",
        "price_raw": "₹18 each",
        "stock": 150,
        "terms": "min order 10",
    },
    {
        "raw_name": "Highlighter Neon Set/5",
        "raw_description": "assorted neon shades",
        "price_raw": "90",
        "stock": 40,
        "terms": None,
    },
    {
        "raw_name": "Wooden Desk Organiser",
        "raw_description": "4-compartment, teak finish",
        "price_raw": "MRP 350",
        "stock": 12,
        "terms": None,
    },
    {
        "raw_name": "correction tape 5mm",
        "raw_description": None,
        "price_raw": "Rs. 35",
        "stock": 80,
        "terms": None,
    },
    {
        "raw_name": "Glue Stick 15g (Dozen)",
        "raw_description": None,
        "price_raw": "₹8/pc, sold dozen only",
        "stock": 20,
        "terms": "min order 1 dozen",
    },
    {
        "raw_name": "Scissors 8\"",
        "raw_description": "stainless steel, office use",
        "price_raw": "55",
        "stock": 25,
        "terms": None,
    },
    {
        "raw_name": "rubber bands (pack)",
        "raw_description": "assorted sizes, 100g",
        "price_raw": "25",
        "stock": 90,
        "terms": None,
    },
    {
        # Deliberately ambiguous: no inferrable single category or fixed contents.
        "raw_name": "Kalam Special Combo Pack",
        "raw_description": "mixed items, ask in store for contents",
        "price_raw": "₹200",
        "stock": 30,
        "terms": "contents vary by stock on hand",
    },
    {
        # Deliberately ambiguous: description contradicts stock=18, and
        # price is a range, not a single number.
        "raw_name": "Pencil Box (assorted)",
        "raw_description": "Currently unavailable — new stock awaited",
        "price_raw": "Rs. 40-65 depending on design",
        "stock": 18,
        "terms": None,
    },
]

DEMO_MANDATE = {
    "agent_id": "demo-buyer-agent-01",
    "spend_cap": 15000,
    "category_scope": ["stationery"],
    "mandate_type": "one_time",
}


def run():
    init_db()
    db = SessionLocal()
    try:
        for sku in RAW_SKUS:
            db.add(CatalogItem(**sku))
        db.add(Mandate(**DEMO_MANDATE))
        db.commit()
        print(f"Seeded {len(RAW_SKUS)} catalog items + 1 mandate.")
    finally:
        db.close()


if __name__ == "__main__":
    run()
