#!/usr/bin/env python3
"""Teinté Release 1: standard-library web/API server for local and small deployments."""
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse, parse_qs, unquote
import datetime as dt, hashlib, hmac, json, os, re, secrets, sqlite3, time, uuid
import unicodedata, difflib
import re as _re

ROOT = Path(__file__).resolve().parent
DB_PATH = Path(os.environ.get('TEINTE_DB_PATH', ROOT / 'data' / 'teinte.sqlite3'))
STATIC = ROOT / 'public'
DEPTHS = ['Fair','Light','Light-Medium','Medium','Medium-Tan','Tan','Deep']
UNDERTONES = ['Cool','Neutral','Warm','Olive']
SOURCE_UNDERTONES = UNDERTONES + ['Neutral-Warm','Warm-Neutral','Neutral-Cool','Cool-Neutral','Neutral-Olive','Olive-Neutral','Warm-Olive','Olive-Warm','Warm-Yellow','Yellow']
MAX_BODY = 12_000
RATE = {}
LOGIN_RATE = {}

def now(): return dt.datetime.now(dt.timezone.utc).isoformat(timespec='seconds')
_pg_url=os.environ.get('TEINTE_DATABASE_URL','')
class SQLiteConnection(sqlite3.Connection):
    def __exit__(self,*args):
        try:return super().__exit__(*args)
        finally:self.close()
class PGConnection:
    def __init__(self,conn): self.conn=conn
    def execute(self,sql,args=()):
        sql=sql.replace('?','%s')
        sql=sql.replace('INSERT OR IGNORE INTO','INSERT INTO')
        if sql.lstrip().upper().startswith('INSERT INTO'): sql=sql.rstrip().rstrip(';')+' ON CONFLICT DO NOTHING'
        args=tuple(uuid.UUID(x) if isinstance(x,str) and _re.fullmatch(r'[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[1-8][0-9a-fA-F]{3}-[89aAbB][0-9a-fA-F]{3}-[0-9a-fA-F]{12}',x) else x for x in args)
        return self.conn.execute(sql,args)
    def __enter__(self): self.conn.__enter__(); return self
    def __exit__(self,*args):
        try: return self.conn.__exit__(*args)
        finally: self.conn.close()
def connect():
    if _pg_url:
        try:
            import psycopg
            from psycopg.rows import dict_row
        except ImportError as e: raise RuntimeError('PostgreSQL runtime requires psycopg 3; install requirements.txt') from e
        return PGConnection(psycopg.connect(_pg_url,row_factory=dict_row))
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(DB_PATH, timeout=15, factory=SQLiteConnection); c.row_factory = sqlite3.Row; c.execute('PRAGMA foreign_keys=ON'); c.execute('PRAGMA busy_timeout=15000'); return c
def init_db():
    if _pg_url:
        sql=(ROOT/'migrations/002_postgres.sql').read_text()
        with connect() as db:
            for statement in sql.split(';'):
                clean='\n'.join(line for line in statement.splitlines() if not line.strip().startswith('--')).strip()
                if clean: db.execute(clean)
    else:
        with connect() as db:
            db.executescript((ROOT/'migrations/001_initial_sqlite.sql').read_text())
            cols={x['name'] for x in db.execute('PRAGMA table_info(shade)').fetchall()}
            for name in ['depth_confidence','undertone_confidence','aliases']:
                if name not in cols:db.execute(f"ALTER TABLE shade ADD COLUMN {name} TEXT NOT NULL DEFAULT 'Unrated'" if name.endswith('confidence') else "ALTER TABLE shade ADD COLUMN aliases TEXT NOT NULL DEFAULT '[]'")
            for table in ['brand','product']:
                existing={x['name'] for x in db.execute(f'PRAGMA table_info({table})').fetchall()}
                if 'aliases' not in existing:db.execute(f"ALTER TABLE {table} ADD COLUMN aliases TEXT NOT NULL DEFAULT '[]'")
        try:
            os.chmod(DB_PATH.parent,0o700); os.chmod(DB_PATH,0o600)
        except OSError: pass
def norm(s):
    s=unicodedata.normalize('NFKD',(s or '').casefold())
    return re.sub(r'[^a-z0-9]+',' ',''.join(c for c in s if not unicodedata.combining(c))).strip()
def undertone_profile_values(value):
    """Return supported profile undertones explicitly named in a source label.

    Keep mixed labels intact for display; this helper only makes their stated
    components eligible for manual profile filtering. Unknown-only labels stay out.
    """
    parts=set(re.findall(r'cool|neutral|warm|olive',norm(value)))
    return parts
def product_type(fmt, name=''):
    value=norm((fmt or '')+' '+(name or ''))
    if 'powder' in value and 'foundation' in value:return 'powder_foundation'
    if 'tint' in value or 'tinted moisturizer' in value:return 'skin_tint'
    if 'foundation' in value:return 'foundation'
    return 'other'
def uid(): return str(uuid.uuid4())
def record(r): return dict(r) if r else None
def rows(q, args=()):
    with connect() as db: return [dict(x) for x in db.execute(q,args).fetchall()]
def row(q,args=()):
    with connect() as db: return record(db.execute(q,args).fetchone())
def match_attribute_consensus(shade_id, attribute):
    """Return an attribute only when direct source-labelled exact claims agree."""
    if attribute not in {'depth','undertone'}: return None
    values=rows(f"""SELECT DISTINCT other.{attribute} value FROM match_claim m
        JOIN shade other ON other.id=CASE WHEN m.source_shade_id=? THEN m.target_shade_id ELSE m.source_shade_id END
        WHERE m.status='Published' AND m.closeness='Source-labelled exact'
        AND (m.source_shade_id=? OR m.target_shade_id=?)
        AND other.publication_state='Published' AND other.lifecycle!='Archived'
        AND other.{attribute} IS NOT NULL""",(shade_id,shade_id,shade_id))
    candidates={x['value'] for x in values if x.get('value')}
    return next(iter(candidates)) if len(candidates)==1 else None
def description_attribute_consensus(description, attribute):
    """Split repeated 'depth | undertone' notes only when all parsed notes agree."""
    if attribute not in {'depth','undertone'}: return None
    depth_labels=('light-to-medium','light-to-deep','medium-to-deep','fair-light','light-medium','medium-tan','medium-deep','tan-deep','fair','light','medium','tan','deep')
    parts=re.split(r'\s*\|\s*',str(description or ''))
    # Some source rows place the two values on either side of a single pipe:
    # "Deep | Neutral". Treat that as a pair only when each side is a clean value.
    if len(parts)==2:
        left=re.sub(r'[–—−]','-',parts[0].casefold()).strip().strip('.,;')
        right=re.sub(r'[–—−]','-',parts[1].casefold()).strip().strip('.,;')
        for label in depth_labels:
            if re.fullmatch(label.replace('-',r'[-\s]+'),left):
                clean_tone=re.sub(r'\s+','-',right).title()
                tone_words=set(re.split(r'[-\s]+',right))
                allowed_tones={'cool','neutral','warm','olive','yellow','golden','pink','red','peach'}
                if clean_tone and tone_words and tone_words<=allowed_tones:
                    return label.title() if attribute=='depth' else clean_tone
    parsed_depths=set(); parsed_tones=set()
    for part in parts:
        note=re.sub(r'[–—−]','-',part.casefold()).strip().strip('.,;')
        for label in depth_labels:
            pattern=label.replace('-',r'[-\s]+')
            found=re.match(r'^('+pattern+r')(?:\s+|\s*[,/:]\s*)(.*)$',note)
            if not found: continue
            depth=found.group(1).replace(' ','-').title()
            parsed_depths.add(depth)
            tone=re.sub(r'\s+','-',found.group(2).strip(' .,:/;')).title()
            if tone and len(tone)<=32: parsed_tones.add(tone)
            break
    values=parsed_depths if attribute=='depth' else parsed_tones
    return next(iter(values)) if len(values)==1 else None
def audit(entity, eid, action, prev, proposed, reason, actor='admin'):
    with connect() as db:
        db.execute('INSERT INTO audit_event VALUES(?,?,?,?,?,?,?,?,?,?)',(uid(),entity,eid,action,json.dumps(prev,ensure_ascii=False,default=str) if prev else None,json.dumps(proposed,ensure_ascii=False,default=str) if proposed else None,reason,actor,'self-review',now()))

class Handler(SimpleHTTPRequestHandler):
    server_version = 'Teinte/1.0'
    def __init__(self,*a,**kw): super().__init__(*a,directory=str(STATIC),**kw)
    def end_headers(self):
        self.send_header('Content-Security-Policy',"default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; font-src 'self' data:; img-src 'self' data:; connect-src 'self'; object-src 'none'; base-uri 'self'; frame-ancestors 'none'; form-action 'self'")
        self.send_header('Referrer-Policy','strict-origin-when-cross-origin')
        self.send_header('Permissions-Policy','camera=(), microphone=(), geolocation=()')
        self.send_header('X-Frame-Options','DENY')
        super().end_headers()
    def log_message(self,fmt,*args):
        # Avoid logging query strings, correction payloads, and optional contact details.
        print('%s %s %s' % (self.log_date_time_string(), self.command, self.path.split('?')[0]))
    def send_json(self, data, status=200, headers=None):
        b=json.dumps(data,ensure_ascii=False,default=str).encode(); self.send_response(status); self.send_header('Content-Type','application/json; charset=utf-8'); self.send_header('Content-Length',str(len(b))); self.send_header('Cache-Control','no-store'); self.send_header('X-Content-Type-Options','nosniff')
        for k,v in (headers or {}).items(): self.send_header(k,v)
        self.end_headers(); self.wfile.write(b)
    def body(self):
        n=int(self.headers.get('Content-Length','0'))
        if n>MAX_BODY: raise ValueError('Request too large')
        return json.loads(self.rfile.read(n) or b'{}')
    def admin(self):
        tok=self.cookies().get('teinte_admin',''); dig=hashlib.sha256(tok.encode()).hexdigest()
        return bool(tok and row('SELECT token_hash FROM admin_session WHERE token_hash=? AND expires_at>?',(dig,now())))
    def cookies(self):
        out={}
        for part in self.headers.get('Cookie','').split(';'):
            if '=' in part:
                k,v=part.strip().split('=',1); out[k]=v
        return out
    def do_GET(self):
        p=urlparse(self.path); path=p.path; q=parse_qs(p.query)
        try:
            if path=='/api/health': return self.send_json({'ok':True,'publication':'review-gated'})
            if path=='/api/search':
                term=norm(q.get('q',[''])[0]);
                if len(term)>120:return self.send_json({'items':[],'reason':'Search terms must be 120 characters or fewer.'},400)
                if len(term)<2: return self.send_json({'items':[],'reason':'Enter at least two characters to search.'})
                candidates=rows("""SELECT s.id shade_id,s.name shade_name,s.aliases shade_aliases,s.code,s.original_description,s.depth,s.undertone,s.availability,s.availability_source,s.availability_checked_at,
                    p.id product_id,p.name product_name,p.aliases product_aliases,p.format,b.id brand_id,b.name brand_name,b.aliases brand_aliases
                    FROM shade s JOIN product p ON p.id=s.product_id JOIN brand b ON b.id=p.brand_id
                    WHERE s.publication_state='Published' AND p.publication_state='Published' AND b.publication_state='Published'
                    AND s.lifecycle!='Archived' AND p.lifecycle!='Archived'""")
                terms=term.split(); result=[]; fmt=q.get('format',[''])[0]; ph=q.get('ph',[''])[0]=='1'
                for x in candidates:
                    if fmt and x.get('format')!=fmt: continue
                    if ph:
                        checked=x.get('availability_checked_at') or ''
                        try:
                            checked_date=dt.date.fromisoformat(checked[:10]); age=(dt.datetime.now(dt.timezone.utc).date()-checked_date).days; fresh=0<=age<=90
                        except Exception: fresh=False
                        if x.get('availability')!='Available in PH' or not x.get('availability_source') or not fresh: continue
                    hay=norm(' '.join(str(x.get(k) or '') for k in ('shade_name','shade_aliases','code','product_name','product_aliases','brand_name','brand_aliases','format','original_description')))
                    tokens=hay.split()
                    def matches(t): return t in hay or t in hay.replace(' ','') or (len(t)>3 and any(difflib.SequenceMatcher(None,t,z).ratio()>=.78 for z in tokens))
                    if all(matches(t) for t in terms): result.append(x)
                result.sort(key=lambda x:(norm(x['brand_name']),norm(x['product_name']),norm(x['shade_name'])))
                return self.send_json({'items':result[:80], 'total':len(result), 'reason':None if result else ('No published entries match these filters.' if fmt or ph else 'No published catalogue entries match. Source records remain under review.')})
            if path=='/api/catalogue':
                return self.send_json({'brands':rows("SELECT id,name FROM brand WHERE publication_state='Published' ORDER BY name"), 'formats':[x['format'] for x in rows("SELECT DISTINCT format FROM product WHERE publication_state='Published' AND format IS NOT NULL ORDER BY format")], 'ph_available':row("SELECT count(*) n FROM shade WHERE publication_state='Published' AND availability='Available in PH' AND availability_source IS NOT NULL")['n'],'count':row("SELECT count(*) n FROM shade WHERE publication_state='Published'")['n']})
            if path=='/api/products':
                term=q.get('q',[''])[0].strip(); kind=q.get('type',[''])[0]
                if kind not in ['skin_tint','foundation','powder_foundation','other']:return self.send_json({'items':[],'reason':'Choose a product type first.'},400)
                if len(term)<2:return self.send_json({'items':[],'reason':'Enter at least two characters to search products.'})
                products=rows("""SELECT p.id product_id,p.name product_name,p.version,p.format,b.name brand_name,b.id brand_id
                  FROM product p JOIN brand b ON b.id=p.brand_id WHERE p.publication_state='Published' AND p.lifecycle!='Archived' AND b.publication_state='Published'
                  AND EXISTS(SELECT 1 FROM shade s WHERE s.product_id=p.id AND s.publication_state='Published' AND s.lifecycle!='Archived') ORDER BY b.name,p.name""")
                terms=norm(term).split(); result=[]
                for x in products:
                    if product_type(x.get('format'),x.get('product_name'))!=kind:continue
                    hay=norm(' '.join(str(x.get(k) or '') for k in ('brand_name','product_name','format','version'))); tokens=hay.split()
                    def matches(t):return t in hay or t in hay.replace(' ','') or (len(t)>3 and any(difflib.SequenceMatcher(None,t,z).ratio()>=.78 for z in tokens))
                    if all(matches(t) for t in terms):result.append(x)
                return self.send_json({'items':result,'total':len(result),'reason':None if result else 'No products of that type matched. Try another search.'})
            if path=='/api/shades':
                return self.send_json({'items':rows("""SELECT s.id shade_id,s.name shade_name,s.code,s.depth,s.undertone,s.original_description,s.availability,
                p.id product_id,p.name product_name,p.format,b.name brand_name FROM shade s JOIN product p ON p.id=s.product_id JOIN brand b ON b.id=p.brand_id
                WHERE s.publication_state='Published' AND p.publication_state='Published' AND b.publication_state='Published' AND s.lifecycle!='Archived' ORDER BY b.name,p.name,s.name""")})
            if path.startswith('/api/brands/'):
                bid=unquote(path.rsplit('/',1)[-1]); brand=row("SELECT id,name,evidence_json FROM brand WHERE id=? AND publication_state='Published'",(bid,))
                if not brand:return self.send_json({'error':'Brand not found.'},404)
                brand['products']=rows("SELECT id,name,version,format FROM product WHERE brand_id=? AND publication_state='Published' AND lifecycle!='Archived' ORDER BY name",(bid,));return self.send_json(brand)
            if path.startswith('/api/products/'):
                pid=unquote(path.rsplit('/',1)[-1]); product=row("SELECT p.id,p.name,p.version,p.format,p.lifecycle,p.evidence_json,b.id brand_id,b.name brand_name,b.evidence_json brand_evidence FROM product p JOIN brand b ON b.id=p.brand_id WHERE p.id=? AND p.publication_state='Published' AND b.publication_state='Published'",(pid,))
                if not product:return self.send_json({'error':'Product not found.'},404)
                product['shades']=rows("SELECT id shade_id,name shade_name,code,original_description,depth,undertone,depth_confidence,undertone_confidence,normalization_state,availability,availability_source,availability_checked_at,evidence_json FROM shade WHERE product_id=? AND publication_state='Published' AND lifecycle!='Archived' ORDER BY name,code",(pid,));return self.send_json(product)
            if path.startswith('/api/shades/'):
                sid=unquote(path.rsplit('/',1)[-1]); shade=row("SELECT s.*,p.name product_name,p.format,p.version,b.id brand_id,b.name brand_name FROM shade s JOIN product p ON p.id=s.product_id JOIN brand b ON b.id=p.brand_id WHERE s.id=? AND s.publication_state='Published' AND p.publication_state='Published' AND b.publication_state='Published' AND s.lifecycle!='Archived'",(sid,))
                if not shade:return self.send_json({'error':'Shade not found.'},404)
                return self.send_json(shade)
            if path=='/api/compare':
                ids=[]
                for part in q.get('ids',[''])[0].split(','):
                    if part and part not in ids: ids.append(part)
                if not 2<=len(ids)<=4:return self.send_json({'items':[],'reason':'Choose two to four published shades to compare.'},400)
                data=rows("""SELECT s.id shade_id,s.name shade_name,s.code,s.original_description,s.depth,s.undertone,s.depth_confidence,s.undertone_confidence,s.evidence_json,s.normalization_state,s.availability,s.availability_source,s.availability_checked_at,
                p.name product_name,p.format,p.version,b.name brand_name FROM shade s JOIN product p ON p.id=s.product_id JOIN brand b ON b.id=p.brand_id
                WHERE s.id IN (%s) AND s.publication_state='Published' AND p.publication_state='Published' AND b.publication_state='Published' AND s.lifecycle!='Archived'""" % ','.join('?'*len(ids)),tuple(ids))
                if len(data)!=len(ids):return self.send_json({'items':[],'reason':'One or more selected shades are unpublished or unavailable.'})
                order={x:i for i,x in enumerate(ids)}; data.sort(key=lambda x:order.get(str(x['shade_id']),len(ids)))
                for x in data:
                    x['depth_from_description']=description_attribute_consensus(x.get('original_description'),'depth') if not x.get('depth') else None
                    x['undertone_from_description']=description_attribute_consensus(x.get('original_description'),'undertone') if not x.get('undertone') else None
                    x['depth_from_matches']=match_attribute_consensus(x['shade_id'],'depth') if not x.get('depth') else None
                    x['undertone_from_matches']=match_attribute_consensus(x['shade_id'],'undertone') if not x.get('undertone') else None
                return self.send_json({'items':data,'method':'Side-by-side shade details; no match is implied.'})
            if path=='/api/matches/known':
                sid=q.get('shade_id',[''])[0]
                source=row("SELECT s.*,p.name product_name,b.name brand_name FROM shade s JOIN product p ON p.id=s.product_id JOIN brand b ON b.id=p.brand_id WHERE s.id=? AND s.publication_state='Published' AND p.publication_state='Published' AND b.publication_state='Published'",(sid,))
                if not source: return self.send_json({'items':[],'reason':'That shade is unavailable or not published.'})
                matches=rows("""SELECT m.id,m.source_wording,m.closeness,m.confidence,m.caveat,m.source_file,m.source_page,m.evidence_json,m.checked_at,
                t.id shade_id,t.name shade_name,t.code,t.depth,t.undertone,t.original_description,t.availability,p.name product_name,p.format,b.name brand_name
                FROM match_claim m JOIN shade t ON t.id=m.target_shade_id JOIN product p ON p.id=t.product_id JOIN brand b ON b.id=p.brand_id
                WHERE m.source_shade_id=? AND m.status='Published' AND t.publication_state='Published' AND p.publication_state='Published' AND b.publication_state='Published' AND t.lifecycle!='Archived' ORDER BY m.confidence DESC""",(sid,))
                for m in matches:
                    m['depth_from_description']=description_attribute_consensus(m.get('original_description'),'depth') if not m.get('depth') else None
                    m['undertone_from_description']=description_attribute_consensus(m.get('original_description'),'undertone') if not m.get('undertone') else None
                    m['depth_from_matches']=match_attribute_consensus(m['shade_id'],'depth') if not m.get('depth') else None
                    m['undertone_from_matches']=match_attribute_consensus(m['shade_id'],'undertone') if not m.get('undertone') else None
                return self.send_json({'source':source,'items':matches,'reason':None if matches else 'No resolvable source-to-target claim is currently available for this shade.'})
            if path=='/api/matches/profile':
                depth=q.get('depth',[''])[0]; tone=q.get('undertone',[''])[0]
                if depth not in DEPTHS or tone not in UNDERTONES: return self.send_json({'items':[],'reason':'Choose one listed depth and undertone.'},400)
                idx=DEPTHS.index(depth); allowed=DEPTHS[max(0,idx-1):min(len(DEPTHS),idx+2)]
                data=rows("""SELECT s.id shade_id,s.name shade_name,s.code,s.depth,s.undertone,s.depth_confidence,s.undertone_confidence,s.evidence_json,s.normalization_state,s.original_description,s.availability,p.name product_name,p.format,b.name brand_name
                  FROM shade s JOIN product p ON p.id=s.product_id JOIN brand b ON b.id=p.brand_id WHERE s.publication_state='Published' AND p.publication_state='Published' AND b.publication_state='Published' AND s.normalization_state IN ('Official','Approved Editorial','Source-derived') AND s.depth IN (%s) AND s.lifecycle!='Archived' ORDER BY b.name,p.name,s.name""" % ','.join('?'*len(allowed)),tuple(allowed))
                main=[]
                for x in data:
                    ev=json.loads(x.get('evidence_json') or '{}')
                    if tone in undertone_profile_values(x.get('undertone')) and x['depth'] in allowed and x['depth_confidence'] in ['High','Moderate','Limited'] and x['undertone_confidence'] in ['High','Moderate','Limited'] and ev.get('depth') and ev.get('undertone'):main.append(x)
                estimate=rows("""SELECT s.id shade_id,s.name shade_name,s.code,s.depth,s.undertone,s.depth_confidence,s.undertone_confidence,s.evidence_json,s.normalization_state,p.name product_name,b.name brand_name FROM shade s JOIN product p ON p.id=s.product_id JOIN brand b ON b.id=p.brand_id WHERE s.publication_state='Published' AND p.publication_state='Published' AND b.publication_state='Published' AND s.normalization_state='Estimated' AND s.depth IN (%s) AND s.lifecycle!='Archived'""" % ','.join('?'*len(allowed)),tuple(allowed))
                estimate=[x for x in estimate if tone in undertone_profile_values(x.get('undertone'))]
                estimate=[x for x in estimate if (lambda ev:bool(ev.get('depth') and ev.get('undertone')))(json.loads(x.get('evidence_json') or '{}'))]
                return self.send_json({'items':main,'estimated':estimate,'policy':'profile-v1','reason':None if main else 'No published shades meet the selected undertone (or include it in a mixed undertone) and exact-or-adjacent depth rules.'})
            if path=='/api/corrections': return self.send_json({'error':'Use POST to submit a correction.'},405)
            if path.startswith('/api/admin/'):
                if not self.admin(): return self.send_json({'error':'Admin authentication required.'},401)
                if path=='/api/admin/overview':
                    return self.send_json({'staged':row('SELECT count(*) n FROM staged_record WHERE disposition="unreviewed"')['n'],'corrections':row('SELECT count(*) n FROM correction_report WHERE status="Under Review"')['n'],'published':row('SELECT count(*) n FROM shade WHERE publication_state="Published"')['n'],'audit':rows('SELECT * FROM audit_event ORDER BY created_at DESC LIMIT 100'),'reports':rows('SELECT id,category,description,contact,status,created_at,retain_until FROM correction_report ORDER BY created_at DESC LIMIT 100')})
                if path=='/api/admin/staged':
                    offset=max(0,int(q.get('offset',['0'])[0]));limit=min(50,max(1,int(q.get('limit',['25'])[0])))
                    return self.send_json({'items':rows('SELECT r.*,f.filename FROM staged_record r JOIN source_file f ON f.id=r.source_file_id ORDER BY r.created_at,r.source_row LIMIT ? OFFSET ?',(limit,offset)),'total':row('SELECT count(*) n FROM staged_record')['n'],'offset':offset,'limit':limit})
                if path=='/api/admin/products': return self.send_json({'items':rows('SELECT p.*,b.name brand_name FROM product p JOIN brand b ON b.id=p.brand_id ORDER BY b.name,p.name')})
                if path=='/api/admin/brands': return self.send_json({'items':rows('SELECT * FROM brand ORDER BY name')})
                if path=='/api/admin/shades': return self.send_json({'items':rows("SELECT s.*,p.name product_name,b.name brand_name FROM shade s JOIN product p ON p.id=s.product_id JOIN brand b ON b.id=p.brand_id WHERE s.lifecycle!='Archived' ORDER BY b.name,p.name,s.name")})
                if path=='/api/admin/claims': return self.send_json({'items':rows("SELECT m.*,ss.name source_shade,sp.name source_product,sb.name source_brand,ts.name target_shade,tp.name target_product,tb.name target_brand FROM match_claim m JOIN shade ss ON ss.id=m.source_shade_id JOIN product sp ON sp.id=ss.product_id JOIN brand sb ON sb.id=sp.brand_id LEFT JOIN shade ts ON ts.id=m.target_shade_id LEFT JOIN product tp ON tp.id=ts.product_id LEFT JOIN brand tb ON tb.id=tp.brand_id WHERE m.status!='Archived' ORDER BY m.created_at DESC")})
                return self.send_json({'error':'Unknown admin route.'},404)
            if path.startswith('/api/'): return self.send_json({'error':'Not found.'},404)
            return super().do_GET()
        except Exception: return self.send_json({'error':'Request could not be completed.'},500)
    def do_POST(self):
        path=urlparse(self.path).path
        try: data=self.body()
        except Exception as e: return self.send_json({'error':str(e)},400)
        if path=='/api/admin/login':
            stamp=time.time(); ip=self.client_address[0]; LOGIN_RATE[ip]=[x for x in LOGIN_RATE.get(ip,[]) if stamp-x<900]
            if len(LOGIN_RATE[ip])>=8:return self.send_json({'error':'Too many sign-in attempts. Try again in 15 minutes.'},429)
            LOGIN_RATE[ip].append(stamp)
            expected=os.environ.get('TEINTE_ADMIN_PASSWORD','')
            if not expected or not hmac.compare_digest(str(data.get('password','')),expected): return self.send_json({'error':'Invalid administrator password.'},401)
            token=secrets.token_urlsafe(32); digest=hashlib.sha256(token.encode()).hexdigest(); exp=(dt.datetime.now(dt.timezone.utc)+dt.timedelta(hours=8)).isoformat(timespec='seconds')
            with connect() as db: db.execute('INSERT INTO admin_session VALUES(?,?,?)',(digest,now(),exp))
            secure='; Secure' if os.environ.get('TEINTE_SECURE_COOKIES')=='1' else ''
            return self.send_json({'ok':True},headers={'Set-Cookie':f'teinte_admin={token}; HttpOnly; SameSite=Strict; Path=/; Max-Age=28800{secure}'})
        if path=='/api/admin/logout':
            tok=self.cookies().get('teinte_admin','');
            with connect() as db: db.execute('DELETE FROM admin_session WHERE token_hash=?',(hashlib.sha256(tok.encode()).hexdigest(),))
            secure='; Secure' if os.environ.get('TEINTE_SECURE_COOKIES')=='1' else ''
            return self.send_json({'ok':True},headers={'Set-Cookie':f'teinte_admin=; HttpOnly; SameSite=Strict; Path=/; Max-Age=0{secure}'})
        if path.startswith('/api/admin/') and not self.admin(): return self.send_json({'error':'Admin authentication required.'},401)
        if path=='/api/corrections':
            ip=self.client_address[0]; stamp=time.time(); RATE[ip]=[x for x in RATE.get(ip,[]) if stamp-x<3600]
            if len(RATE[ip])>=5: return self.send_json({'error':'Please try again later.'},429)
            RATE[ip].append(stamp)
            # Honeypot field; do not persist its contents.
            if data.get('website'): return self.send_json({'ok':True,'reference':'received'})
            category=str(data.get('category','')).strip(); description=str(data.get('description','')).strip(); contact=str(data.get('contact','')).strip() or None
            if category not in ['Wrong shade','Missing product or shade','Stale information','Incorrect source','Other'] or not 15<=len(description)<=3000 or (contact and (len(contact)>254 or not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+",contact))): return self.send_json({'error':'Check the category, description (15–3,000 characters), and optional contact.'},400)
            rid='TNT-'+secrets.token_hex(4).upper(); retain=(dt.datetime.now(dt.timezone.utc)+dt.timedelta(days=90)).isoformat(timespec='seconds')
            with connect() as db: db.execute('INSERT INTO correction_report VALUES(?,?,?,?,?,?,?,?,?)',(rid,category,description,contact,'Under Review',now(),retain,None,None))
            return self.send_json({'ok':True,'reference':rid},201)
        if path=='/api/admin/review-stage':
            rid=str(data.get('id','')); disposition=str(data.get('disposition','')); reason=str(data.get('reason','')).strip()
            if disposition not in ['needs_attention','excluded','reviewed_candidate'] or len(reason)<5:return self.send_json({'error':'Choose a disposition and give a review reason.'},400)
            old=row('SELECT * FROM staged_record WHERE id=?',(rid,))
            if not old:return self.send_json({'error':'Staged record not found.'},404)
            with connect() as db: db.execute('UPDATE staged_record SET disposition=?,disposition_reason=? WHERE id=?',(disposition,reason,rid))
            audit('staged_record',rid,'review-disposition',old,{'disposition':disposition,'reason':reason},reason)
            return self.send_json({'ok':True})
        if path=='/api/admin/review-correction':
            rid=str(data.get('id','')); status=data.get('status'); reason=str(data.get('reason','')).strip(); old=row('SELECT * FROM correction_report WHERE id=?',(rid,))
            if not old or status not in ['Under Review','Resolved','Rejected'] or len(reason)<5:return self.send_json({'error':'Report, valid status and reason are required.'},400)
            with connect() as db: db.execute('UPDATE correction_report SET status=?,admin_notes=?,resolved_at=? WHERE id=?',(status,reason,now() if status!='Under Review' else None,rid))
            old_for_audit={k:v for k,v in old.items() if k!='contact'}
            audit('correction_report',rid,'review',old_for_audit,{'status':status},reason); return self.send_json({'ok':True})
        if path=='/api/admin/update-shade':
            sid=str(data.get('id','')); reason=str(data.get('reason','')).strip(); old=row('SELECT * FROM shade WHERE id=?',(sid,))
            if not old:return self.send_json({'error':'Shade not found.'},404)
            allowed={'name','aliases','code','original_description','depth','undertone','normalization_state','depth_confidence','undertone_confidence','availability','availability_source','availability_checked_at','evidence_json'}
            changes={k:data[k] for k in allowed if k in data}
            if isinstance(changes.get('aliases'),list):changes['aliases']=json.dumps(changes['aliases'],ensure_ascii=False)
            if 'evidence' in data: changes['evidence_json']=json.dumps(data['evidence'],ensure_ascii=False)
            if 'field_evidence' in data:changes['evidence_json']=json.dumps(data['field_evidence'],ensure_ascii=False)
            if not changes or len(reason)<5:return self.send_json({'error':'At least one field and an audit reason are required.'},400)
            new_evidence=data.get('field_evidence')
            evidence_fields={'name':'name','code':'code','original_description':'description','depth':'depth','undertone':'undertone','availability':'availability'}
            if any(k in changes and changes[k]!=old.get(k) for k in evidence_fields) and not isinstance(new_evidence,dict):return self.send_json({'error':'Changing a sourced field requires updated field-specific evidence references.'},409)
            if isinstance(new_evidence,dict):
                for field,col in evidence_fields.items():
                    if col in new_evidence and not isinstance(new_evidence[col],list):return self.send_json({'error':f'Evidence references for {field} must be a list.'},400)
            if 'depth' in changes and changes['depth'] not in DEPTHS and changes['depth'] is not None:return self.send_json({'error':'Depth is not in the approved taxonomy.'},400)
            if 'undertone' in changes and changes['undertone'] not in SOURCE_UNDERTONES and changes['undertone'] is not None:return self.send_json({'error':'Undertone is not in the approved source labels.'},400)
            if 'normalization_state' in changes and changes['normalization_state'] not in ['Official','Approved Editorial','Estimated','Incomplete','Source-derived']:return self.send_json({'error':'Unknown normalization state.'},400)
            if any(changes.get(k) not in ['High','Moderate','Limited','Unrated'] for k in ['depth_confidence','undertone_confidence'] if k in changes):return self.send_json({'error':'Invalid evidence confidence.'},400)
            if 'availability' in changes and changes['availability'] not in ['Available in PH','Imported','Limited availability','Discontinued','Not Verified']:return self.send_json({'error':'Unknown availability state.'},400)
            if 'aliases' in changes and not isinstance(json.loads(changes['aliases']),list):return self.send_json({'error':'Aliases must be a list.'},400)
            if 'evidence_json' in changes:
                try:
                    evidence=json.loads(changes['evidence_json']);
                    if not isinstance(evidence,dict):raise ValueError()
                except Exception:return self.send_json({'error':'Field evidence must be a field-to-reference object.'},400)
            assignments=','.join(f'{k}=?' for k in changes)
            with connect() as db: db.execute(f'UPDATE shade SET {assignments},updated_at=? WHERE id=?',(*changes.values(),now(),sid))
            audit('shade',sid,'edit',old,changes,reason); return self.send_json({'ok':True})
        if path=='/api/admin/update-record':
            typ=data.get('entity_type'); rid=str(data.get('id','')); reason=str(data.get('reason','')).strip()
            configs={'brand':('brand',{'name','aliases','status','evidence_json'}),'product':('product',{'name','aliases','version','format','lifecycle','evidence_json'})}
            if typ not in configs or len(reason)<5:return self.send_json({'error':'Valid brand/product type and audit reason are required.'},400)
            table,allowed=configs[typ]; old=row(f'SELECT * FROM {table} WHERE id=?',(rid,)); changes={k:data[k] for k in allowed if k in data}
            if typ=='brand' and old and 'name' in changes and changes['name']!=old.get('name') and not isinstance(data.get('field_evidence'),dict):return self.send_json({'error':'Changing brand identity requires updated identity evidence.'},409)
            if typ=='product' and old and any(k in changes and changes[k]!=old.get(k) for k in ['version','format']) and not isinstance(data.get('field_evidence'),dict):return self.send_json({'error':'Changing product version or format requires updated field evidence.'},409)
            if 'field_evidence' in data:
                prior=json.loads(old['evidence_json'] or '{}') if old else {};prior.update(data['field_evidence']);changes['evidence_json']=json.dumps(prior,ensure_ascii=False)
            if isinstance(changes.get('aliases'),list):changes['aliases']=json.dumps(changes['aliases'],ensure_ascii=False)
            if not old or not changes:return self.send_json({'error':'Record not found or there are no editable fields.'},404)
            assigns=','.join(f'{k}=?' for k in changes)
            with connect() as db:db.execute(f'UPDATE {table} SET {assigns},updated_at=? WHERE id=?',(*changes.values(),now(),rid))
            audit(typ,rid,'edit',old,changes,reason);return self.send_json({'ok':True})
        if path=='/api/admin/archive-record':
            typ=data.get('entity_type'); rid=str(data.get('id','')); reason=str(data.get('reason','')).strip()
            tables={'brand':'brand','product':'product','shade':'shade','match_claim':'match_claim'}
            if typ not in tables or len(reason)<5:return self.send_json({'error':'Valid record type and archive reason are required.'},400)
            table=tables[typ];old=row(f'SELECT * FROM {table} WHERE id=?',(rid,))
            if not old:return self.send_json({'error':'Record not found.'},404)
            changes={'publication_state':'Archived'} if typ in ['brand','product','shade'] else {'status':'Archived'}
            if typ=='brand':changes['status']='archived'
            if typ in ['product','shade']:changes['lifecycle']='Archived'
            assignments=','.join(f'{k}=?' for k in changes)
            suffix=',updated_at=?' if typ in ['brand','product','shade'] else ''
            params=(*changes.values(),*((now(),) if suffix else ()),rid)
            with connect() as db:db.execute(f'UPDATE {table} SET {assignments}{suffix} WHERE id=?',params)
            audit(typ,rid,'archive',old,changes,reason);return self.send_json({'ok':True})
        if path=='/api/admin/archive-shade':
            sid=str(data.get('id','')); reason=str(data.get('reason','')).strip(); old=row('SELECT * FROM shade WHERE id=?',(sid,))
            if not old or len(reason)<5:return self.send_json({'error':'Shade and archive reason are required.'},400)
            with connect() as db: db.execute("UPDATE shade SET publication_state='Archived',lifecycle='Archived',updated_at=? WHERE id=?",(now(),sid))
            audit('shade',sid,'archive',old,{'publication_state':'Archived','lifecycle':'Archived'},reason); return self.send_json({'ok':True})
        if path=='/api/admin/rollback':
            eid=str(data.get('event_id','')); reason=str(data.get('reason','')).strip(); ev=row('SELECT * FROM audit_event WHERE id=?',(eid,))
            if not ev or len(reason)<5:return self.send_json({'error':'Audit event and rollback reason are required.'},400)
            tables={'brand':('brand',{'name','aliases','status','publication_state','evidence_json'}),'product':('product',{'name','version','format','lifecycle','publication_state','evidence_json'}),'shade':('shade',{'name','code','original_description','depth','undertone','normalization_state','availability','availability_source','availability_checked_at','publication_state','lifecycle','evidence_json'}),'match_claim':('match_claim',{'source_wording','closeness','confidence','caveat','source_file','source_page','evidence_json','status','checked_at'})}
            if ev['entity_type'] not in tables:return self.send_json({'error':'This record type cannot be rolled back by this endpoint.'},409)
            table,allowed=tables[ev['entity_type']]; current=row(f'SELECT * FROM {table} WHERE id=?',(ev['entity_id'],))
            if not current:return self.send_json({'error':'Referenced record no longer exists.'},404)
            previous=json.loads(ev['previous_json']) if ev['previous_json'] else None
            proposed={k:previous[k] for k in allowed if previous and k in previous}
            if not proposed: proposed={'publication_state':'Archived','lifecycle':'Archived'} if ev['entity_type'] in ['shade','product','brand'] else {'status':'Draft'}
            updates={k:v for k,v in proposed.items() if k in allowed}
            assigns=','.join(f'{k}=?' for k in updates)
            with connect() as db: db.execute(f'UPDATE {table} SET {assigns} WHERE id=?',(*updates.values(),ev['entity_id']))
            audit(ev['entity_type'],str(ev['entity_id']),'rollback',current,updates,reason); return self.send_json({'ok':True,'restored':updates})
        if path=='/api/admin/brands':
            name=str(data.get('name','')).strip(); reason=str(data.get('reason','')).strip()
            if not name or not reason:return self.send_json({'error':'Name and audit reason are required.'},400)
            bid=uid(); item={'id':bid,'name':name}
            evidence={'identity':[str(x).strip()[:500] for x in data.get('evidence',[]) if str(x).strip()]}
            aliases=data.get('aliases',[]);aliases=aliases.splitlines() if isinstance(aliases,str) else aliases
            if not isinstance(aliases,list) or len(name)>200:return self.send_json({'error':'Brand name or aliases are invalid.'},400)
            with connect() as db: db.execute('INSERT INTO brand(id,name,aliases,evidence_json,updated_at) VALUES(?,?,?,?,?)',(bid,name,json.dumps(aliases,ensure_ascii=False),json.dumps(evidence),now()))
            audit('brand',bid,'create',None,item,reason); return self.send_json(item,201)
        if path=='/api/admin/products':
            brand_id=str(data.get('brand_id','')); name=str(data.get('name','')).strip(); reason=str(data.get('reason','')).strip()
            if not row('SELECT id FROM brand WHERE id=?',(brand_id,)) or not name or not reason:return self.send_json({'error':'Select an existing brand and provide product name and audit reason.'},400)
            pid=uid()
            fe=data.get('field_evidence',{});fe=fe if isinstance(fe,dict) else {};evidence={'identity':[str(x).strip()[:500] for x in data.get('evidence',[]) if str(x).strip()],'version':fe.get('version',[]),'format':fe.get('format',[])};aliases=data.get('aliases',[]);aliases=aliases.splitlines() if isinstance(aliases,str) else aliases
            if not isinstance(aliases,list) or len(name)>250:return self.send_json({'error':'Product name or aliases are invalid.'},400)
            with connect() as db: db.execute('INSERT INTO product(id,brand_id,name,aliases,version,format,evidence_json,updated_at) VALUES(?,?,?,?,?,?,?,?)',(pid,brand_id,name,json.dumps(aliases,ensure_ascii=False),data.get('version'),data.get('format'),json.dumps(evidence),now()))
            audit('product',pid,'create',None,data,reason); return self.send_json({'id':pid},201)
        if path=='/api/admin/shades':
            product_id=str(data.get('product_id','')); name=str(data.get('name','')).strip(); reason=str(data.get('reason','')).strip()
            if not row('SELECT id FROM product WHERE id=?',(product_id,)) or not name or not reason:return self.send_json({'error':'Select a product and provide shade name and audit reason.'},400)
            if data.get('depth') not in DEPTHS+[None,''] or data.get('undertone') not in SOURCE_UNDERTONES+[None,''] or data.get('normalization_state','Incomplete') not in ['Official','Approved Editorial','Estimated','Incomplete','Source-derived'] or data.get('depth_confidence','Unrated') not in ['High','Moderate','Limited','Unrated'] or data.get('undertone_confidence','Unrated') not in ['High','Moderate','Limited','Unrated']:
                return self.send_json({'error':'Shade attributes must use the approved taxonomy and states.'},400)
            sid=uid()
            field_evidence=data.get('field_evidence',{})
            if not isinstance(field_evidence,dict):return self.send_json({'error':'Field evidence must be supplied separately by attribute.'},400)
            ev={k:[str(x).strip()[:500] for x in field_evidence.get(k,[]) if str(x).strip()] for k in ['identity','name','code','description','depth','undertone','availability']}
            aliases=data.get('aliases',[]);aliases=aliases.splitlines() if isinstance(aliases,str) else aliases
            if not isinstance(aliases,list):return self.send_json({'error':'Shade aliases must be a list.'},400)
            with connect() as db: db.execute('INSERT INTO shade(id,product_id,name,aliases,code,original_description,depth,undertone,normalization_state,depth_confidence,undertone_confidence,availability,availability_source,availability_checked_at,evidence_json,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',(sid,product_id,name,json.dumps(aliases,ensure_ascii=False),data.get('code'),data.get('original_description'),data.get('depth'),data.get('undertone'),data.get('normalization_state','Incomplete'),data.get('depth_confidence','Unrated'),data.get('undertone_confidence','Unrated'),data.get('availability','Not Verified'),data.get('availability_source') or None,data.get('availability_checked_at') or None,json.dumps(ev),now()))
            audit('shade',sid,'create',None,data,reason); return self.send_json({'id':sid},201)
        if path=='/api/admin/claims':
            src=str(data.get('source_shade_id','')); tgt=str(data.get('target_shade_id','')) or None; wording=str(data.get('source_wording','')).strip(); reason=str(data.get('reason','')).strip()
            if not row("SELECT id FROM shade WHERE id=? AND publication_state='Published'",(src,)) or (tgt and not row("SELECT id FROM shade WHERE id=? AND publication_state='Published'",(tgt,))) or not wording or not reason:
                return self.send_json({'error':'Select published source and target shades, preserve source wording, and provide a reason.'},400)
            if len(wording)>1500 or data.get('confidence','Unrated') not in ['High','Moderate','Limited','Unrated'] or data.get('closeness','Unnormalized') not in ['Unnormalized','Source-labelled exact','Closest equivalent','Close/adjacent','Slightly lighter','Slightly darker','Undertone differs','Outside useful range','Insufficient evidence']:
                return self.send_json({'error':'Claim wording or normalized confidence/closeness is invalid.'},400)
            cid=uid()
            with connect() as db: db.execute('INSERT INTO match_claim(id,source_shade_id,target_shade_id,source_wording,closeness,confidence,caveat,source_file,source_page,evidence_json,status,checked_at,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)',(cid,src,tgt,wording,data.get('closeness'),data.get('confidence','Unrated'),data.get('caveat'),data.get('source_file'),data.get('source_page'),json.dumps(data.get('evidence',[])), 'Draft',None,now()))
            audit('match_claim',cid,'create',None,data,reason); return self.send_json({'id':cid},201)
        if path=='/api/admin/publish-claim':
            cid=str(data.get('id','')); reason=str(data.get('reason','')).strip(); m=row('SELECT * FROM match_claim WHERE id=?',(cid,))
            if not m:return self.send_json({'error':'Claim not found.'},404)
            valid=bool(m['target_shade_id'] and m['source_wording'].strip() and m['source_file'] and m['source_page'] and json.loads(m['evidence_json'] or '[]') and row("SELECT id FROM shade WHERE id=? AND publication_state='Published'",(m['source_shade_id'],)) and row("SELECT id FROM shade WHERE id=? AND publication_state='Published'",(m['target_shade_id'],)))
            if not valid or len(reason)<5:return self.send_json({'error':'Publication blocked: claim needs exact published endpoints, original wording, source file/page, evidence references, and an audit reason.'},409)
            with connect() as db: db.execute("UPDATE match_claim SET status='Published',checked_at=? WHERE id=?",(now(),cid))
            audit('match_claim',cid,'publish',m,{'status':'Published'},reason); return self.send_json({'ok':True})
        if path=='/api/admin/publish-brand':
            bid=str(data.get('id',''));reason=str(data.get('reason','')).strip();b=row('SELECT * FROM brand WHERE id=?',(bid,))
            ev=json.loads(b['evidence_json'] or '{}') if b else {}
            if not b:return self.send_json({'error':'Brand not found.'},404)
            if len(reason)<5 or not ev.get('identity'):return self.send_json({'error':'Publication requires brand identity evidence and an audit reason.'},409)
            with connect() as db:db.execute("UPDATE brand SET publication_state='Published',status='active',updated_at=? WHERE id=?",(now(),bid))
            audit('brand',bid,'publish',b,{'publication_state':'Published'},reason);return self.send_json({'ok':True})
        if path=='/api/admin/publish-product':
            pid=str(data.get('id',''));reason=str(data.get('reason','')).strip();p=row('SELECT * FROM product WHERE id=?',(pid,))
            ev=json.loads(p['evidence_json'] or '{}') if p else {}
            if not p:return self.send_json({'error':'Product not found.'},404)
            attrs=json.loads(p['evidence_json'] or '{}')
            if len(reason)<5 or not attrs.get('identity') or (p['version'] and not attrs.get('version')) or (p['format'] and not attrs.get('format')) or not row("SELECT id FROM brand WHERE id=? AND publication_state='Published'",(p['brand_id'],)):return self.send_json({'error':'Publication requires published brand identity, product identity evidence, evidence for any stated version/format, and an audit reason.'},409)
            with connect() as db:db.execute("UPDATE product SET publication_state='Published',lifecycle='Active',updated_at=? WHERE id=?",(now(),pid))
            audit('product',pid,'publish',p,{'publication_state':'Published'},reason);return self.send_json({'ok':True})
        if path=='/api/admin/publish-shade':
            sid=str(data.get('id','')); reason=str(data.get('reason','')).strip(); s=row('SELECT * FROM shade WHERE id=?',(sid,))
            if not s:return self.send_json({'error':'Shade not found.'},404)
            evidence=json.loads(s['evidence_json'] or '{}'); p=row('SELECT * FROM product WHERE id=?',(s['product_id'],)); b=row('SELECT * FROM brand WHERE id=?',(p['brand_id'],)) if p else None
            identity=bool(s['name'] and evidence.get('identity') and evidence.get('name') and (not s['code'] or evidence.get('code')) and (not s['original_description'] or evidence.get('description')) and p and b and p['publication_state']=='Published' and b['publication_state']=='Published')
            reviewed=s['normalization_state'] in ['Official','Approved Editorial','Source-derived']
            attributes=(s['depth'] in DEPTHS and s['undertone'] in UNDERTONES and bool(evidence.get('depth')) and bool(evidence.get('undertone')) and s['depth_confidence'] in ['High','Moderate','Limited'] and s['undertone_confidence'] in ['High','Moderate','Limited'])
            fresh_availability=False
            if s['availability_checked_at']:
                try:
                    av_age=(dt.datetime.now(dt.timezone.utc).date()-dt.date.fromisoformat(s['availability_checked_at'][:10])).days;fresh_availability=0<=av_age<=90
                except Exception:fresh_availability=False
            availability_ok=s['availability']!='Available in PH' or (s['availability_source'] and fresh_availability and evidence.get('availability'))
            valid_review=attributes if reviewed else s['normalization_state'] in ['Incomplete','Estimated']
            if not reason or not identity or not valid_review or not availability_ok:return self.send_json({'error':'Publication blocked. Check brand/product publication, shade identity and name evidence, reviewed attribute evidence/confidence when normalized, PH availability evidence, and audit reason. Incomplete or estimated records remain labeled and cannot enter main profile results.','checks':{'identity_and_parent_records':identity,'normalized_attributes':attributes if reviewed else 'not approved for main profile results','availability_evidence':bool(availability_ok)}},409)
            with connect() as db: db.execute("UPDATE shade SET publication_state='Published',updated_at=? WHERE id=?",(now(),sid))
            audit('shade',sid,'publish',s,{'publication_state':'Published'},reason); return self.send_json({'ok':True})
        if path.startswith('/api/admin/'):return self.send_json({'error':'Unknown admin route.'},404)
        return self.send_json({'error':'Not found.'},404)
    def do_DELETE(self):
        # Catalog objects are archived through review; no hard-delete route exists.
        return self.send_json({'error':'Referenced catalogue records are archived, not hard-deleted.'},405)

if __name__=='__main__':
    init_db(); host=os.environ.get('HOST','127.0.0.1'); port=int(os.environ.get('PORT','8000'))
    if not os.environ.get('TEINTE_ADMIN_PASSWORD'): print('WARNING: set TEINTE_ADMIN_PASSWORD before using protected admin routes.')
    print(f'Teinté listening on http://{host}:{port}'); ThreadingHTTPServer((host,port),Handler).serve_forever()
