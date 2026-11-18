#!/usr/bin/env python3
"""Include every Product/shade/Depth/Undertone entry from the supplied text.

Exact product-scoped records are updated in place. A uniquely code-identified
record under the wrong product is reattached with an audit event. Missing product
or shade records are created from the supplied data. All rows remain explicitly
Source-derived with Limited confidence and source-line provenance.
"""
import collections
import datetime as dt
import hashlib
import json
import re
import sqlite3
import sys
import unicodedata
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DB = ROOT / "data" / "teinte.sqlite3"
SOURCE_DEFAULT = Path("/Users/glaireann/.codex/attachments/98ead933-1521-43db-beb6-848e81f47fdb/Pasted text.txt")
REPORT = ROOT / "data" / "user-attribute-import-report.json"
PRODUCT_MAP = {
    "Absidy Vital Blur": ("Absidy", "Vital Blur Filter Skin Tint"),
    "BLK Cosmetics Universal Skin Tint Sun Shield SPF 30": ("BLK Cosmetics", "Universal Skin Tint Sun Shield SPF 30"),
    "BLK Fresh Radiant Glow Foundation": ("BLK Cosmetics", "Fresh Radiant Glow Foundation"),
    "Careline Oil Control Skin Tint": ("Careline", "Oil Control Skin Tint"),
    "Colourette First Base": ("Colourette", "First Base Everyday Skin Tint"),
    "Dazzle Me Acne Armor Cover & Correct Skin Tint": ("Dazzle Me", "Acne Armor Cover & Correct Skin Tint"),
    "Detail Cosmetics Fresh Filter Foundation": ("Detail Cosmetics", "Fresh Filter Foundation"),
    "EB Plus Good Skin Day Skin Tint": ("EB Plus", "Good Skin Day Skin Tint"),
    "Ellana Loose Mineral SkinShield Foundation SPF 16": ("Ellana", "Loose Mineral SkinShield Foundation SPF 16"),
    "Ever Bilena All Day Matte Liquid Foundation": ("Ever Bilena", "All Day Matte Liquid Foundation"),
    "GRWM Daily Skin Tint SPF 30": ("GRWM Cosmetics", "All About That Base Daily Skin Tint Broad Spectrum SPF 30 PA+++"),
    "GRWM Fresh Matte": ("GRWM Cosmetics", "All About That Base Fresh Matte Multiuse Base"),
    "GRWM Radiant Skin Luminous Foundation": ("GRWM Cosmetics", "Radiant Skin Luminous Foundation"),
    "Happy Skin Matte Serum Cushion": ("Happy Skin", "Second Skin Matte Serum Cushion Foundation SPF 15"),
    "Happy Skin Blurring Skin Tint SPF 20": ("Generation Happy Skin", "Blurring Skin Tint SPF 20"),
    "Human Nature Soft Glow Skin Tint": ("Human Nature", "Soft Glow Skin Tint"),
    "ISSY Active Foundation": ("ISSY", "Active Foundation"),
    "ISSY Active Skin Tint SPF 35": ("ISSY", "Active Skin Tint SPF 35"),
    "ISSY True Flex Powder Foundation": ("ISSY", "True Flex Powder Foundation"),
    "Lucky Beauty Skin Blur Powder Foundation": ("Lucky Beauty", "Skin Blur Powder Foundation"),
    "SOLA Foundation Primer": ("SOLA Beauty", "Foundation Primer"),
    "Strokes Silk Veil Powder Foundation": ("Strokes Beauty Lab", "Silk Veil Powder Foundation"),
    "Strokes Soft Veil Filter Foundation": ("Strokes Beauty Lab", "Soft Veil Filter Foundation"),
    "Sunnies Face Skin So Good Daily Tinted Oil-Free Moisturizer SPF 20": ("Sunnies Face", "Skin So Good Daily Tinted Oil-Free Moisturizer SPF 20"),
    "VICE Cosmetics Endlezz Dream Focus Weightless Foundation": ("Vice Cosmetics", "Endlezz Dream Focus Weightless Foundation"),
    "Vice Cosmetics SUNWEAR Infinite Blur Skin Tint SPF 50 PA++++": ("Vice Cosmetics", "SUNWEAR Infinite Blur Skin Tint SPF 50 PA++++"),
}


def now():
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


def norm(value):
    value = unicodedata.normalize("NFKD", (value or "").casefold())
    return re.sub(r"[^a-z0-9]+", " ", "".join(c for c in value if not unicodedata.combining(c))).strip()


def canonical_label(value):
    return "-".join(part[:1].upper() + part[1:].lower() for part in value.strip().split("-"))


def parse(source):
    result, current = [], {}
    for line_no, line in enumerate(source.read_text(encoding="utf-8").splitlines(), 1):
        value = line.strip()
        if value.startswith("Product:"):
            current = {"product_label": value.split(":", 1)[1].strip(), "line": line_no}
        elif value.startswith("shade:"):
            current["shade_label"] = value.split(":", 1)[1].strip()
        elif value.startswith("Depth:"):
            current["depth"] = value.split(":", 1)[1].strip()
        elif value.startswith("Undertone:"):
            current["undertone"] = value.split(":", 1)[1].strip()
            if all(current.get(k) for k in ("product_label", "shade_label", "depth", "undertone")):
                result.append(current)
            current = {}
    return result


def split_code(label):
    match = re.match(r"^([A-Z]{1,4}\d+(?:\.\d+)?)\s+(.+)$", label.strip(), re.I)
    return (match.group(1), match.group(2).strip()) if match else (None, label.strip())


def source_identity_matches(shade, label):
    code, name = split_code(label)
    values = [shade["name"], shade["code"] or "", f"{shade['name']} {shade['code'] or ''}", f"{shade['code'] or ''} {shade['name']}"]
    if any(norm(label) == norm(v) for v in values if v):
        return True
    if code and ((shade["code"] and norm(shade["code"]) == norm(code)) or re.match(r"^" + re.escape(code) + r"(?:\s|$)", shade["name"], re.I)):
        return True
    return False


def parse_evidence(value):
    try:
        obj = json.loads(value or "{}")
        return obj if isinstance(obj, dict) else {"prior_evidence": obj}
    except (TypeError, json.JSONDecodeError):
        return {}


def audit(con, entity_type, entity_id, action, previous, proposed, reason):
    con.execute("INSERT INTO audit_event VALUES(?,?,?,?,?,?,?,?,?,?)", (
        str(uuid.uuid4()), entity_type, entity_id, action,
        json.dumps(previous, ensure_ascii=False) if previous is not None else None,
        json.dumps(proposed, ensure_ascii=False), reason, "user-provided data import", "self-review", now(),
    ))


def resolve_product(con, label, products, brands, actions, source_sha):
    mapping = PRODUCT_MAP.get(label)
    if label == "Teviant":
        candidates = [p for p in products.values() if p["brand"] == "Teviant"]
        if len(candidates) != 1:
            raise ValueError(f"Cannot map the brand-only source label {label!r} to exactly one database product.")
        return candidates[0]
    if not mapping:
        raise ValueError(f"No explicit product mapping for {label!r}.")
    product = products.get(mapping)
    if product:
        return product
    brand_name, product_name = mapping
    brand = brands.get(brand_name)
    if not brand:
        raise ValueError(f"Source brand {brand_name!r} is not present in the database.")
    # The only missing product is explicitly named in the supplied data. Publish it
    # because the user asked for every supplied item to appear on the website.
    product_id = str(uuid.uuid4())
    refs = [f"Pasted text.txt line {next(e['line'] for e in actions['entries'] if e['product_label'] == label)} (SHA-256 {source_sha[:12]})"]
    evidence = {"identity": refs, "version": [], "format": [], "user_supplied_identity": label}
    con.execute("INSERT INTO product(id,brand_id,name,version,format,lifecycle,publication_state,evidence_json,updated_at,aliases) VALUES(?,?,?,?,?,?,?,?,?,?)",
                (product_id, brand["id"], product_name, None, "Foundation", "Source-derived", "Published", json.dumps(evidence, ensure_ascii=False), now(), json.dumps([label], ensure_ascii=False)))
    audit(con, "product", product_id, "create_and_publish_from_user_basis", None, {"brand": brand_name, "name": product_name, "publication_state": "Published", "evidence": evidence}, "Include explicitly named product and supplied shade rows on the website, as requested.")
    product = dict(con.execute("SELECT p.*,b.name brand FROM product p JOIN brand b ON b.id=p.brand_id WHERE p.id=?", (product_id,)).fetchone())
    products[(brand_name, product_name)] = product
    actions["created_products"].append({"brand": brand_name, "product": product_name, "product_id": product_id})
    return product


def main():
    source = Path(sys.argv[1]) if len(sys.argv) > 1 else SOURCE_DEFAULT
    if not source.is_file():
        raise SystemExit(f"Source text file not found: {source}")
    entries = parse(source)
    if not entries:
        raise SystemExit("No complete Product/shade/Depth/Undertone entries found; database left unchanged.")
    if len({(e["product_label"], norm(e["shade_label"])) for e in entries}) != len(entries):
        raise SystemExit("Duplicate product/shade identities in source; database left unchanged for manual review.")
    source_sha = hashlib.sha256(source.read_bytes()).hexdigest()
    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup = ROOT / "data" / "backups" / f"teinte-before-all-source-shades-{stamp}.sqlite3"
    backup.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys=ON")
    dest = sqlite3.connect(backup)
    con.backup(dest)
    dest.close()
    products = {(r["brand"], r["name"]): dict(r) for r in con.execute("SELECT p.*,b.name brand FROM product p JOIN brand b ON b.id=p.brand_id")}
    brands = {r["name"]: dict(r) for r in con.execute("SELECT * FROM brand")}
    actions = {"entries": entries, "created_products": [], "created_shades": [], "reassigned_shades": [], "updated_shades": [], "already_applied": [], "errors": []}
    shades = [dict(r) for r in con.execute("SELECT * FROM shade")]
    applied_ids = set()
    con.execute("BEGIN IMMEDIATE")
    try:
        for entry in entries:
            product = resolve_product(con, entry["product_label"], products, brands, actions, source_sha)
            product_id = product["id"]
            target_matches = [s for s in shades if s["product_id"] == product_id and source_identity_matches(s, entry["shade_label"])]
            if len(target_matches) > 1:
                # Exact input product and shade is still authoritative; retain the
                # first stable row and leave duplicate archived rows untouched.
                active = [s for s in target_matches if s["publication_state"] == "Published" and s["lifecycle"] != "Archived"]
                if len(active) == 1:
                    target_matches = active
                else:
                    raise ValueError(f"Ambiguous duplicate records for {entry['product_label']} / {entry['shade_label']}.")
            if target_matches:
                shade = target_matches[0]
            else:
                # Move a record only when its exact shade/code identity is unique
                # across the existing catalogue; otherwise create a product-scoped row.
                source_code, _ = split_code(entry["shade_label"])
                global_matches = [s for s in shades if source_code and source_identity_matches(s, entry["shade_label"])
                                  and s["publication_state"] == "Published" and s["lifecycle"] != "Archived"]
                if source_code and len(global_matches) == 1 and global_matches[0]["product_id"] != product_id:
                    shade = global_matches[0]
                    previous = dict(shade)
                    con.execute("UPDATE shade SET product_id=?,publication_state='Published',lifecycle='Active',updated_at=? WHERE id=?", (product_id, now(), shade["id"]))
                    shade.update({"product_id": product_id, "publication_state": "Published", "lifecycle": "Active"})
                    audit(con, "shade", shade["id"], "reassign_and_publish_from_user_basis", previous,
                          {"product_id": product_id, "brand": product["brand"], "product": product["name"], "publication_state": "Published"},
                          f"Use the exact product and shade identity from Pasted text.txt line {entry['line']}.")
                    actions["reassigned_shades"].append({"shade_id": shade["id"], "shade": shade["name"], "from_product_id": previous["product_id"], "to_product": product["name"], "source_line": entry["line"]})
                else:
                    code, name = split_code(entry["shade_label"])
                    # Split a recognizable shade code only for a new record. Keep
                    # un-coded source labels intact as shade names.
                    shade_id = str(uuid.uuid4())
                    shade = {"id": shade_id, "product_id": product_id, "name": name, "code": code,
                             "publication_state": "Published", "lifecycle": "Active"}
                    refs = [f"Pasted text.txt line {entry['line']} (SHA-256 {source_sha[:12]})"]
                    evidence = {"identity": refs, "name": refs, "code": refs if code else [], "description": [],
                                "depth": refs, "undertone": refs, "availability": [],
                                "user_supplied_attribute_basis": [{"source_file": source.name, "source_line": entry["line"], "product_as_supplied": entry["product_label"], "shade_as_supplied": entry["shade_label"], "depth_as_supplied": entry["depth"], "undertone_as_supplied": entry["undertone"], "sha256": source_sha}]}
                    con.execute("""INSERT INTO shade(id,product_id,name,code,original_description,depth,undertone,normalization_state,availability,publication_state,lifecycle,evidence_json,updated_at,depth_confidence,undertone_confidence,aliases)
                                  VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                                (shade_id, product_id, name, code, entry["shade_label"], canonical_label(entry["depth"]), canonical_label(entry["undertone"]), "Source-derived", "Not Verified", "Published", "Active", json.dumps(evidence, ensure_ascii=False), now(), "Limited", "Limited", "[]"))
                    shade.update({"depth": canonical_label(entry["depth"]), "undertone": canonical_label(entry["undertone"]), "normalization_state": "Source-derived", "depth_confidence": "Limited", "undertone_confidence": "Limited", "evidence_json": json.dumps(evidence, ensure_ascii=False)})
                    audit(con, "shade", shade_id, "create_and_publish_from_user_basis", None, {"brand": product["brand"], "product": product["name"], "shade": name, "code": code, "depth": shade["depth"], "undertone": shade["undertone"], "publication_state": "Published", "evidence": evidence}, f"Include explicitly listed shade on the website using Pasted text.txt line {entry['line']}.")
                    shades.append(shade)
                    actions["created_shades"].append({"shade_id": shade_id, "brand": product["brand"], "product": product["name"], "shade": name, "code": code, "source_line": entry["line"]})
            if shade["id"] in applied_ids:
                raise ValueError(f"Multiple source entries resolve to shade ID {shade['id']}.")
            applied_ids.add(shade["id"])
            previous_depth, previous_tone = shade.get("depth"), shade.get("undertone")
            depth, tone = canonical_label(entry["depth"]), canonical_label(entry["undertone"])
            evidence = parse_evidence(shade.get("evidence_json"))
            refs = f"Pasted text.txt line {entry['line']} (SHA-256 {source_sha[:12]})"
            for field in ("depth", "undertone"):
                field_refs = evidence.get(field, [])
                if not isinstance(field_refs, list): field_refs = [str(field_refs)] if field_refs else []
                if refs not in field_refs: field_refs.append(refs)
                evidence[field] = field_refs
            basis = {"source_file": source.name, "source_line": entry["line"], "product_as_supplied": entry["product_label"], "shade_as_supplied": entry["shade_label"], "depth_as_supplied": entry["depth"], "undertone_as_supplied": entry["undertone"], "sha256": source_sha}
            bases = evidence.setdefault("user_supplied_attribute_basis", [])
            if basis not in bases: bases.append(basis)
            previous_row = dict(shade)
            con.execute("UPDATE shade SET depth=?,undertone=?,normalization_state='Source-derived',depth_confidence='Limited',undertone_confidence='Limited',publication_state='Published',lifecycle='Active',evidence_json=?,updated_at=? WHERE id=?",
                        (depth, tone, json.dumps(evidence, ensure_ascii=False), now(), shade["id"]))
            shade.update({"depth": depth, "undertone": tone, "normalization_state": "Source-derived", "depth_confidence": "Limited", "undertone_confidence": "Limited", "publication_state": "Published", "lifecycle": "Active", "evidence_json": json.dumps(evidence, ensure_ascii=False)})
            if previous_depth != depth or previous_tone != tone or basis not in parse_evidence(previous_row.get("evidence_json")).get("user_supplied_attribute_basis", []):
                audit(con, "shade", shade["id"], "update_and_publish_attributes_from_user_basis", previous_row,
                      {"depth": depth, "undertone": tone, "normalization_state": "Source-derived", "confidence": "Limited", "publication_state": "Published", "evidence_added": refs},
                      f"Apply user-provided depth and undertone; include this listed item on the website (Pasted text.txt line {entry['line']}).")
                actions["updated_shades"].append({"shade_id": shade["id"], "brand": product["brand"], "product": product["name"], "shade": shade["name"], "depth": depth, "undertone": tone, "source_line": entry["line"]})
            else:
                actions["already_applied"].append({"shade_id": shade["id"], "product": product["name"], "shade": shade["name"], "source_line": entry["line"]})
        con.commit()
    except Exception:
        con.rollback()
        con.close()
        backup.unlink(missing_ok=True)
        raise
    con.close()

    report = {
        "source_file": str(source), "source_sha256": source_sha, "source_entries": len(entries),
        "website_included_entries": len(applied_ids), "created_products": actions["created_products"],
        "product_mapping_note": "The source uses only the brand name 'Teviant'; it was mapped to the sole Teviant product currently in the database.",
        "created_shades": actions["created_shades"], "reassigned_shades": actions["reassigned_shades"],
        "updated_shades": actions["updated_shades"], "already_applied": actions["already_applied"],
        "backup_file": str(backup),
    }
    REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({k: (len(v) if isinstance(v, list) else v) for k, v in report.items() if k not in ("created_products", "created_shades", "reassigned_shades", "updated_shades", "already_applied")}, ensure_ascii=False, indent=2))
    for key in ("created_products", "created_shades", "reassigned_shades", "updated_shades", "already_applied"):
        print(f"{key}: {len(actions[key])}")


if __name__ == "__main__":
    main()
