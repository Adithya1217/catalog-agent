"""
run_enrichment.py -- one-time offline enrichment pass (Phase 3).

Loops over every seeded CatalogItem, calls enrich_item() once per row,
writes the result into that row's enriched columns, and logs one
"enrichment" audit event per item. Run this once, ahead of the demo --
GET /catalog only ever reads what's already stored; it never calls the
LLM live per-request.

Usage:
    python run_enrichment.py
"""

import audit
from enrich import enrich_item
from models import CatalogItem, SessionLocal


def run():
    db = SessionLocal()
    try:
        items = db.query(CatalogItem).order_by(CatalogItem.id).all()
        for item in items:
            result = enrich_item(
                raw_name=item.raw_name,
                raw_description=item.raw_description,
                price_raw=item.price_raw,
                stock=item.stock,
                terms=item.terms,
            )

            item.clean_description = result["clean_description"]
            item.category = result["category"]
            item.tags = result["tags"]
            item.price = result["price"]
            item.enrichment_reasoning = result["reasoning"]
            item.enrichment_confidence = result["confidence"]

            audit.log_event(
                actor="merchant_system",
                action="enrichment",
                decision="info",
                reason=result["reasoning"],
                reasoning_basis=result["confidence"],
                linked_entity_type="catalog_item",
                linked_entity_id=item.id,
                db=db,
            )

            print(f"[{item.id:2}] {item.raw_name!r}")
            print(f"     -> category={result['category']!r} price={result['price']} confidence={result['confidence']}")
            print(f"     -> reasoning: {result['reasoning']}")

        db.commit()
        print(f"\nEnriched {len(items)} catalog items.")
    finally:
        db.close()


if __name__ == "__main__":
    run()
