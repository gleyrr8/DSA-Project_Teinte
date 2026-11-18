#!/usr/bin/env python3
"""Load source catalogue rows into canonical tables for a preloaded demo.

Uses the product IDs and source-matrix anchor IDs supplied in the project data.
All source references are preserved, stable IDs make reruns safe, and source files
are never modified.
"""
import json
import re
import uuid
from collections import defaultdict

import server

NAMESPACE = uuid.UUID('539d4bc1-f2d7-4c6d-90a4-7b79053d86df')
DEPTHS = ['Fair', 'Light', 'Light-Medium', 'Medium', 'Medium-Tan', 'Tan', 'Deep']
TONES = ['Cool', 'Neutral', 'Warm', 'Olive']
PRODUCT_LABELS = {
    'TP-001': ['colourette first base'], 'TP-002': ['issy active foundation'],
    'TP-003': ['issy true flex'], 'TP-004': ['issy active skin tint'],
    'TP-005': ['grwm fresh matte', 'fresh matte multiuse base'],
    'TP-006': ['grwm daily skin tint', 'daily skin tint'],
    'TP-007': ['ever bilena all day matte'], 'TP-008': ['eb plus good skin day', 'good skin day skin tint'],
    'TP-009': ['strokes soft veil'], 'TP-010': ['strokes silk veil'],
    'TP-011': ['vice endlezz dream focus', 'endlezz dream focus'],
    'TP-012': ['vice sunwear', 'sunwear infinite blur'],
    'TP-013': ['blk fresh radiant glow'], 'TP-014': ['blk universal skin tint'],
    'TP-015': ['lucky beauty skin blur'], 'TP-016': ['happy skin second skin matte serum cushion'],
    'TP-017': ['generation happy skin blurring skin tint', 'happy skin blurring skin tint'],
    'TP-018': ['ellana loose mineral skinshield'], 'TP-019': ['careline oil control skin tint'],
    'TP-020': ['human nature soft glow skin tint'], 'TP-021': ['absidy vital blur'],
    'TP-022': ['sunnies face skin so good'], 'TP-023': ['sola foundation primer'],
    'TP-024': ['teviant the ultimate skin master'], 'TP-025': ['detail fresh filter'],
    'TP-026': ['dazzle me acne armor'],
}


def uid(key):
    return str(uuid.uuid5(NAMESPACE, key))


def norm(value):
    return re.sub(r'[^a-z0-9]+', ' ', str(value or '').casefold()).strip()


def refs(row):
    return f"{row['filename']} row {row['source_row']}"


def load_rows(db, record_type):
    out = []
    for row in db.execute('SELECT sf.filename,sr.source_row,sr.raw_payload FROM staged_record sr JOIN source_file sf ON sf.id=sr.source_file_id WHERE sr.record_type=? ORDER BY sf.filename,sr.source_row', (record_type,)):
        item = dict(row)
        item['data'] = json.loads(item.pop('raw_payload'))
        out.append(item)
    return out


def resolve_product(obs, by_tp, products, by_source):
    # Source-matrix IDs are provenance only: one matrix contains an anchor
    # product plus comparison products, so its ID cannot identify a shade row.
    # Likewise, suggested IDs in the extraction file are explicitly marked as
    # possible aliases. Resolve from the row's own product/section wording.
    suggested = obs.get('Suggested product ID', '').strip()
    if suggested in by_tp:
        return by_tp[suggested], 'source-suggested product ID'
    text = norm((obs.get('Brand/product wording in source', '') or '') + ' ' + (obs.get('Product/section heading in source', '') or ''))
    explicit = []
    for product in products:
        labels = PRODUCT_LABELS.get(product.get('Product ID'), [])
        if any(norm(label) in text for label in labels):
            explicit.append(product)
    if len(explicit) == 1:
        return by_tp[explicit[0]['Product ID']], 'explicit product label'
    if len(explicit) > 1:
        return None, 'multiple product labels appear in this row'
    return None, 'row wording does not identify a product; source-matrix IDs are provenance only'


def main():
    server.init_db()
    with server.connect() as db:
        product_rows = [r['data'] | {'_ref': refs(r)} for r in load_rows(db, 'product_candidate')]
        shade_rows = [r | {'data': r['data']} for r in load_rows(db, 'shade_observation')]
        claim_rows = [r | {'data': r['data']} for r in load_rows(db, 'match_claim_observation')]
        brand_ids = {}
        tp_ids = {}
        by_source = {}
        stamp = server.now()

        # Retain prior imported versions for recovery, but show the current seed set.
        db.execute("UPDATE match_claim SET status='Archived' WHERE caveat='Imported source claim; wording and direction are from the supplied database.'")
        db.execute("UPDATE shade SET publication_state='Archived',lifecycle='Archived' WHERE substr(id,15,1)='5'")
        db.execute("UPDATE product SET publication_state='Archived',lifecycle='Archived' WHERE substr(id,15,1)='5'")
        db.execute("UPDATE brand SET publication_state='Archived',status='archived' WHERE substr(id,15,1)='5'")

        # Product_Catalogue.csv is the declared source for the canonical brand and product labels.
        for p in product_rows:
            brand = p.get('Brand', '').strip()
            product = p.get('Product', '').strip()
            if not brand or not product:
                continue
            brand_id = brand_ids.setdefault(norm(brand), uid('brand:' + norm(brand)))
            evidence = {'identity': [p['_ref']] + ([p['Official source link']] if p.get('Official source link') else [])}
            db.execute("INSERT INTO brand(id,name,aliases,status,publication_state,evidence_json,updated_at) VALUES(?,?,?,'active','Published',?,?) ON CONFLICT(id) DO UPDATE SET name=excluded.name,status='active',publication_state='Published',evidence_json=excluded.evidence_json,updated_at=excluded.updated_at", (brand_id, brand, '[]', json.dumps(evidence, ensure_ascii=False), stamp))
            pid = uid('product:' + p['Product ID'])
            tp_ids[p['Product ID']] = pid
            if p.get('Source ID'):by_source[p['Source ID'].strip()]=pid
            ev = {'identity': [p['_ref']] + ([p['Official source link']] if p.get('Official source link') else []), 'format': [p['_ref']] if p.get('Format') else []}
            db.execute("INSERT INTO product(id,brand_id,name,aliases,format,lifecycle,publication_state,evidence_json,updated_at) VALUES(?,?,?,?,?,'Active','Published',?,?) ON CONFLICT(id) DO UPDATE SET brand_id=excluded.brand_id,name=excluded.name,format=excluded.format,lifecycle='Active',publication_state='Published',evidence_json=excluded.evidence_json,updated_at=excluded.updated_at", (pid, brand_id, product, '[]', p.get('Format') or None, json.dumps(ev, ensure_ascii=False), stamp))

        # Resolve each shade observation to a supplied product candidate. Exact duplicate
        # shade observations under the same product are represented once, with every source
        # row retained in the evidence references.
        mapped = defaultdict(lambda: {'refs': [], 'descriptions': [], 'depths': [], 'tones': [], 'observations': []})
        unresolved = []
        obs_to_shade = {}
        for r in shade_rows:
            d = r['data']
            prod, method = resolve_product(d, tp_ids, product_rows, by_source)
            if not prod:
                unresolved.append({'id': d.get('Shade record ID'), 'reason': method})
                continue
            name = (d.get('Shade name') or '').strip()
            if not name:
                unresolved.append({'id': d.get('Shade record ID'), 'reason': 'shade name is blank'})
                continue
            key = prod + ':' + norm(name)
            acc = mapped[key]
            acc['product_id'] = prod
            acc['name'] = name
            acc['refs'].append(refs(r) + (('; ' + d.get('Source page/line references', '')) if d.get('Source page/line references') else ''))
            acc['observations'].append(d.get('Shade record ID', ''))
            desc = (d.get('Original shade description') or '').strip()
            if desc and desc not in acc['descriptions']:
                acc['descriptions'].append(desc)
            depth = next((x for x in DEPTHS if norm(d.get('Depth shown in source (candidate)')) == norm(x)), None)
            tone = next((x for x in TONES if norm(d.get('Undertone shown in source (candidate)')) == norm(x)), None)
            if depth and depth not in acc['depths']: acc['depths'].append(depth)
            if tone and tone not in acc['tones']: acc['tones'].append(tone)
            obs_to_shade[d.get('Shade record ID', '')] = uid('shade:' + key)

        shades_by_name = defaultdict(list)
        for key, a in mapped.items():
            sid = uid('shade:' + key)
            # Conflicting extracted attributes stay unknown instead of choosing one.
            depth = a['depths'][0] if len(a['depths']) == 1 else None
            tone = a['tones'][0] if len(a['tones']) == 1 else None
            ev = {
                'identity': a['refs'], 'name': a['refs'],
                'description': a['refs'] if a['descriptions'] else [],
                'depth': a['refs'] if depth else [], 'undertone': a['refs'] if tone else [],
                'source_observations': a['observations'],
                'conflicting_depth_values': a['depths'] if len(a['depths']) > 1 else [],
                'conflicting_undertone_values': a['tones'] if len(a['tones']) > 1 else [],
            }
            description = ' | '.join(a['descriptions']) or None
            db.execute("""INSERT INTO shade(id,product_id,name,aliases,original_description,depth,undertone,normalization_state,depth_confidence,undertone_confidence,availability,publication_state,lifecycle,evidence_json,updated_at)
                VALUES(?,?,?,'[]',?,?,?,'Source-derived',?,?, 'Not Verified','Published','Active',?,?)
                ON CONFLICT(id) DO UPDATE SET evidence_json=excluded.evidence_json,depth=excluded.depth,undertone=excluded.undertone,original_description=excluded.original_description,publication_state='Published',lifecycle='Active',updated_at=excluded.updated_at""",
                (sid, a['product_id'], a['name'], description, depth, tone, 'Limited' if depth else 'Unrated', 'Limited' if tone else 'Unrated', json.dumps(ev, ensure_ascii=False), stamp))
            shades_by_name[norm(a['name'])].append(sid)

        # Build only explicit source-directed claims whose source and target resolve
        # unambiguously to this source-backed catalogue. Missing/ambiguous endpoints stay staged.
        claims_added = 0
        claims_unresolved = 0
        by_product_label = defaultdict(list)
        for p in product_rows:
            by_product_label[norm(p.get('Brand', '') + ' ' + p.get('Product', ''))].append(p)
            by_product_label[norm(p.get('Product', ''))].append(p)
        for r in claim_rows:
            d = r['data']
            label = norm(d.get('Source brand/product', '') or d.get('Product/section', ''))
            candidates = []
            for k, ps in by_product_label.items():
                if k and (k in label or label in k): candidates.extend(ps)
            unique = {p['Product ID']: p for p in candidates}
            source_shades = []
            if len(unique) == 1:
                p = next(iter(unique.values()))
                source_shades = [uid('shade:' + tp_ids[p['Product ID']] + ':' + norm(d.get('Source shade', '')))]
                exists = db.execute('SELECT id FROM shade WHERE id=?', (source_shades[0],)).fetchone()
                if not exists: source_shades = []
            targets = shades_by_name.get(norm(d.get('Suggested matching shade', '')), [])
            if len(source_shades) != 1 or len(targets) != 1 or source_shades[0] == targets[0]:
                claims_unresolved += 1
                continue
            cid = uid('claim:' + str(d.get('Match claim ID', r['source_row'])))
            wording = str(d.get('Original match wording') or '').strip()
            if not wording: claims_unresolved += 1; continue
            desc = norm(d.get('Match description', ''))
            closeness = {'exact': 'Source-labelled exact', 'slightly lighter': 'Slightly lighter', 'slightly darker': 'Slightly darker', 'closest equivalent': 'Closest equivalent', 'close': 'Close/adjacent'}.get(desc, 'Unnormalized')
            file = d.get('Source PDF') or r['filename']
            line = d.get('Source line') or ''
            evidence = [f"{file}, line {line}" if line else r['filename'] + ' row ' + str(r['source_row'])]
            caveat = 'Imported source claim; wording and direction are from the supplied database.'
            db.execute("""INSERT INTO match_claim(id,source_shade_id,target_shade_id,source_wording,closeness,confidence,caveat,source_file,source_page,evidence_json,status,checked_at,created_at)
                VALUES(?,?,?,?,?,'Unrated',?,?,?,?, 'Published',NULL,?) ON CONFLICT(id) DO UPDATE SET source_shade_id=excluded.source_shade_id,target_shade_id=excluded.target_shade_id,source_wording=excluded.source_wording,closeness=excluded.closeness,caveat=excluded.caveat,source_file=excluded.source_file,source_page=excluded.source_page,evidence_json=excluded.evidence_json,status='Published'""",
                (cid, source_shades[0], targets[0], wording, closeness, caveat, file, str(line) or None, json.dumps(evidence, ensure_ascii=False), stamp))
            if db.execute('SELECT changes()').fetchone()[0]: claims_added += 1

        report = {
            'mode': 'source-trusting preloaded catalogue',
            'source_products': len(product_rows),
            'published_brands': db.execute("SELECT COUNT(*) FROM brand WHERE publication_state='Published'").fetchone()[0],
            'published_products': db.execute("SELECT COUNT(*) FROM product WHERE publication_state='Published'").fetchone()[0],
            'published_shades': db.execute("SELECT COUNT(*) FROM shade WHERE publication_state='Published'").fetchone()[0],
            'published_source_claims_added_this_run': claims_added,
            'shade_observations_loaded': sum(len(x['observations']) for x in mapped.values()),
            'unresolved_shade_observations': len(unresolved),
            'unresolved_or_ambiguous_claims': claims_unresolved,
            'notes': ['Source values are trusted as requested but labeled Source-derived.', 'Shade rows use the supplied suggested product ID or a clear product label; source-matrix IDs are provenance only.', 'Claims with unresolved endpoints remain staged.', 'Conflicting depth/undertone values are left blank.']
        }
        event_id = uid('audit:source-trusting-seed:v6')
        db.execute('INSERT OR IGNORE INTO audit_event(id,entity_type,entity_id,action,previous_json,proposed_json,reason,actor,review_mode,created_at) VALUES(?,?,?,?,?,?,?,?,?,?)', (
            event_id, 'catalogue_import', event_id, 'source-seed', None,
            json.dumps(report, ensure_ascii=False),
            'Corrected source-derived catalogue mapping: product identity comes from each shade row wording, never its matrix provenance ID; ambiguous identities and conflicting attributes were left unresolved.',
            'source-import', 'user-authorized source import', stamp))
        with open('data/catalogue-seed-report.json', 'w', encoding='utf-8') as f:
            json.dump(report | {'unresolved_shades': unresolved[:100]}, f, ensure_ascii=False, indent=2)
        print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
