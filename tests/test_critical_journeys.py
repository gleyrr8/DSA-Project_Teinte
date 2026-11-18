import json, os, tempfile, threading, unittest, uuid
from http.server import ThreadingHTTPServer
from urllib.request import Request, urlopen, quote
from urllib.error import HTTPError
import sys
sys.path.insert(0,os.path.dirname(os.path.dirname(__file__)))
import server

class Journeys(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp=tempfile.TemporaryDirectory(); server.DB_PATH=__import__('pathlib').Path(cls.tmp.name)/'test.sqlite3'
        os.environ['TEINTE_ADMIN_PASSWORD']='test-only-admin-password';server.init_db()
        cls.http=ThreadingHTTPServer(('127.0.0.1',0),server.Handler);cls.thread=threading.Thread(target=cls.http.serve_forever,daemon=True);cls.thread.start();cls.base=f'http://127.0.0.1:{cls.http.server_port}'
        cls.ids={k:str(uuid.uuid4()) for k in ['b1','b2','p1','p2','p3','s1','s2','s3','s4','s5','s6','s7','s8','claim1','claim2','claim3','claim4','reverse','stage']}
        with server.connect() as db:
            for bid,name,aliases in [(cls.ids['b1'],'Téinté Beauty','["Tint House"]'),(cls.ids['b2'],'Other Brand','[]')]:db.execute("INSERT INTO brand(id,name,aliases,publication_state,updated_at) VALUES(?,?,?,'Published',?)",(bid,name,aliases,server.now()))
            for pid,bid,name,aliases,fmt in [(cls.ids['p1'],cls.ids['b1'],'Base One','["Everyday"]','Liquid foundation'),(cls.ids['p2'],cls.ids['b2'],'Base Two','[]','Skin tint'),(cls.ids['p3'],cls.ids['b2'],'Base Three','[]','Powder foundation')]:db.execute("INSERT INTO product(id,brand_id,name,aliases,format,publication_state,lifecycle,updated_at) VALUES(?,?,?,?,?,'Published','Active',?)",(pid,bid,name,aliases,fmt,server.now()))
            shades=[('s1','p1','Mati','M1','Fair','Cool','Official','Published','Active'),('s2','p2','Mati','M1','Light','Cool','Official','Published','Active'),('s3','p3','Mati','M1','Medium','Cool','Official','Published','Active'),('s4','p2','Warm One','W1','Deep','Warm','Official','Published','Active'),('s5','p3','Unknown One','U1','Deep',None,'Incomplete','Published','Active'),('s6','p3','Hidden','H1','Fair','Cool','Official','Draft','Active'),('s7','p3','Tan One','T1','Tan','Warm','Official','Published','Active'),('s8','p3','Medium One','M2','Medium','Warm','Official','Published','Active')]
            for sk,pk,name,code,depth,tone,norm,pub,life in shades:
                ev=json.dumps({'identity':['A-01 page 2'],'name':['A-01 page 2'],'depth':['A-01 page 2'] if depth else [],'undertone':['A-01 page 2'] if tone else []})
                aliases='["M-First"]' if sk=='s1' else '[]'
                db.execute("INSERT INTO shade(id,product_id,name,aliases,code,depth,undertone,normalization_state,depth_confidence,undertone_confidence,publication_state,lifecycle,evidence_json,updated_at) VALUES(?,?,?,?,?,?,?,?,'High','High',?,?,?,?)",(cls.ids[sk],cls.ids[pk],name,aliases,code,depth,tone,norm,pub,life,ev,server.now()))
            db.execute("INSERT INTO match_claim(id,source_shade_id,target_shade_id,source_wording,closeness,confidence,source_file,source_page,evidence_json,status,created_at) VALUES(?,?,?,?,?,?,?,?,?,'Published',?)",(cls.ids['claim1'],cls.ids['s1'],cls.ids['s2'],'Exact — Mati','Source-labelled exact','Limited','matrix.pdf','4',json.dumps(['A-01 page 4']),server.now()))
            db.execute("INSERT INTO match_claim(id,source_shade_id,target_shade_id,source_wording,closeness,confidence,source_file,source_page,evidence_json,status,created_at) VALUES(?,?,?,?,?,?,?,?,?,'Published',?)",(cls.ids['claim2'],cls.ids['s1'],cls.ids['s3'],'Slightly lighter — Mati','Slightly lighter','Moderate','matrix-2.pdf','8',json.dumps(['A-02 page 8']),server.now()))
            db.execute("INSERT INTO match_claim(id,source_shade_id,target_shade_id,source_wording,closeness,confidence,evidence_json,status,created_at) VALUES(?,?,?,?,?,?,?,'Draft',?)",(cls.ids['reverse'],cls.ids['s2'],cls.ids['s1'],'Reverse draft','Source-labelled exact','High',json.dumps(['A-01']),server.now()))
            db.execute("INSERT INTO match_claim(id,source_shade_id,target_shade_id,source_wording,closeness,confidence,evidence_json,status,created_at) VALUES(?,?,?,?,?,?,?,'Published',?)",(cls.ids['claim3'],cls.ids['s2'],cls.ids['s4'],'A different source edge','Close/adjacent','Limited',json.dumps(['A-03']),server.now()))
            db.execute("INSERT INTO match_claim(id,source_shade_id,target_shade_id,source_wording,closeness,confidence,evidence_json,status,created_at) VALUES(?,?,NULL,?,?,?,?,'Published',?)",(cls.ids['claim4'],cls.ids['s3'],'Unresolved target wording','Insufficient evidence','Unrated',json.dumps(['A-04']),server.now()))
    @classmethod
    def tearDownClass(cls):cls.http.shutdown();cls.http.server_close();cls.tmp.cleanup()
    def request(self,path,data=None,cookie=None):
        req=Request(self.base+path,data=json.dumps(data).encode() if data is not None else None,headers={'Content-Type':'application/json',**({'Cookie':cookie} if cookie else {})})
        try:
            with urlopen(req) as r:return r.status,json.loads(r.read()),r.headers
        except HTTPError as e:return e.code,json.loads(e.read()),e.headers
    def test_guest_search_normalization_duplicate_names_and_no_results(self):
        code,d,_=self.request('/api/search?q='+quote('teintebeauty mati'));self.assertEqual(code,200);self.assertEqual(len(d['items']),1);self.assertEqual(d['items'][0]['shade_name'],'Mati')
        _,d,_=self.request('/api/search?q=matti');self.assertGreaterEqual(d['total'],3)
        _,d,_=self.request('/api/search?q=everyday%20m-first');self.assertEqual(d['total'],1)
        _,d,_=self.request('/api/search?q=hidden');self.assertEqual(d['total'],0)
        _,d,_=self.request('/api/search?q=not-in-catalogue');self.assertIn('No published',d['reason'])
        _,d,_=self.request('/api/search?q=mati&format=Powder');self.assertEqual(d['total'],0)
        _,d,_=self.request('/api/search?q=mati&ph=1');self.assertEqual(d['total'],0)
        _,cmp,_=self.request('/api/compare?ids='+self.ids['s1']+','+self.ids['s2']);self.assertEqual(len(cmp['items']),2)
        _,product,_=self.request('/api/products/'+self.ids['p1']);self.assertEqual(len(product['shades']),1)
        _,brand,_=self.request('/api/brands/'+self.ids['b1']);self.assertEqual(len(brand['products']),1)
    def test_known_product_type_filter_groups_before_shade_selection(self):
        code,d,_=self.request('/api/products?type=foundation&q=other%20brand');self.assertEqual(code,200);self.assertEqual(d['items'],[])
        _,d,_=self.request('/api/products?type=skin_tint&q=other%20brand');self.assertEqual([x['product_id'] for x in d['items']],[self.ids['p2']])
        _,d,_=self.request('/api/products?type=powder_foundation&q=other%20brand');self.assertEqual([x['product_id'] for x in d['items']],[self.ids['p3']])
    def test_known_match_is_directed_and_conflicts_are_preserved(self):
        _,d,_=self.request('/api/matches/known?shade_id='+self.ids['s1']);self.assertEqual(len(d['items']),2);self.assertEqual({x['source_wording'] for x in d['items']},{'Exact — Mati','Slightly lighter — Mati'});self.assertNotIn(self.ids['s4'],{x['shade_id'] for x in d['items']})
        _,reverse,_=self.request('/api/matches/known?shade_id='+self.ids['s2']);self.assertEqual(len(reverse['items']),1);self.assertEqual(reverse['items'][0]['shade_id'],self.ids['s4']);self.assertNotIn(self.ids['s1'],{x['shade_id'] for x in reverse['items']})
        _,missing,_=self.request('/api/matches/known?shade_id='+self.ids['s3']);self.assertEqual(missing['items'],[])
        _,missing,_=self.request('/api/matches/known?shade_id='+self.ids['s6']);self.assertEqual(missing['items'],[])
    def test_profile_rules_unknown_undertone_and_boundaries(self):
        _,fair,_=self.request('/api/matches/profile?depth=Fair&undertone=Cool');self.assertEqual({x['shade_name'] for x in fair['items']},{'Mati'})
        _,deep,_=self.request('/api/matches/profile?depth=Deep&undertone=Warm');self.assertEqual({x['depth'] for x in deep['items']},{'Deep','Tan'});self.assertNotIn('Medium',{x['depth'] for x in deep['items']})
        _,unknown,_=self.request('/api/matches/profile?depth=Deep&undertone=Neutral');self.assertNotIn('Unknown One',{x['shade_name'] for x in unknown['items']})
        code,d,_=self.request('/api/matches/profile?depth=Fair&undertone=Unknown');self.assertEqual(code,400)
    def test_source_derived_imports_can_feed_profile_results(self):
        with server.connect() as db:db.execute("UPDATE shade SET normalization_state='Source-derived' WHERE id=?",(self.ids['s8'],))
        code,d,_=self.request('/api/matches/profile?depth=Medium&undertone=Warm');self.assertEqual(code,200)
        self.assertIn('Medium One',{x['shade_name'] for x in d['items']})
    def test_admin_gate_publication_corrections_and_abuse_limit(self):
        self.assertEqual(self.request('/api/admin/overview')[0],401)
        code,_,headers=self.request('/api/admin/login',{'password':os.environ['TEINTE_ADMIN_PASSWORD']});self.assertEqual(code,200);cookie=headers.get('Set-Cookie').split(';',1)[0]
        code,_,_=self.request('/api/admin/publish-shade',{'id':str(uuid.uuid4()),'reason':'review'},cookie);self.assertEqual(code,404)
        code,_,_=self.request('/api/admin/overview',cookie=cookie);self.assertEqual(code,200)
        for i in range(5):
            code,d,_=self.request('/api/corrections',{'category':'Wrong shade','description':f'Please review this catalogue shade detail {i}.','contact':''});self.assertEqual(code,201,d)
        code,_,_=self.request('/api/corrections',{'category':'Wrong shade','description':'Please review this catalogue shade detail beyond limit.','contact':''});self.assertEqual(code,429)
        with server.connect() as db:self.assertEqual(db.execute('SELECT count(*) FROM shade WHERE publication_state="Published"').fetchone()[0],7)
    def test_contact_retention_cleanup(self):
        import maintenance,datetime
        exp=(datetime.datetime.now(datetime.timezone.utc)-datetime.timedelta(days=1)).isoformat(timespec='seconds')
        with server.connect() as db:db.execute("INSERT INTO correction_report VALUES(?,?,?,?,?,?,?,?,?)",('TNT-OLD','Other','Expired test report','old@example.invalid','Resolved',server.now(),exp,None,server.now()))
        maintenance.main()
        with server.connect() as db:self.assertIsNone(db.execute("SELECT contact FROM correction_report WHERE id='TNT-OLD'").fetchone()[0])
    def test_import_retry_is_idempotent(self):
        with server.connect() as db:
            db.execute("INSERT INTO source_file VALUES(?,?,?,?,?,?)",(str(uuid.uuid4()),'test.csv','a'*64,server.now(),'test',1))
            sid=db.execute("SELECT id FROM source_file WHERE filename='test.csv'").fetchone()[0]
            rid=str(uuid.uuid4());payload='{"raw":"source"}'
            db.execute("INSERT OR IGNORE INTO staged_record VALUES(?,?,?,?,?,'unreviewed',NULL,?)",(rid,sid,'match_claim_observation',2,payload,server.now()))
            db.execute("INSERT OR IGNORE INTO staged_record VALUES(?,?,?,?,?,'unreviewed',NULL,?)",(rid,sid,'match_claim_observation',2,payload,server.now()))
            self.assertEqual(db.execute("SELECT count(*) FROM staged_record WHERE source_file_id=?",(sid,)).fetchone()[0],1)
    def test_editorial_publication_and_directed_claim_flow(self):
        _,_,headers=self.request('/api/admin/login',{'password':os.environ['TEINTE_ADMIN_PASSWORD']});cookie=headers.get('Set-Cookie').split(';',1)[0]
        _,brand,_=self.request('/api/admin/brands',{'name':'Gate Brand','aliases':[],'evidence':['official brand source'], 'reason':'Create reviewed brand'},cookie)
        self.assertEqual(self.request('/api/admin/publish-brand',{'id':brand['id'],'reason':'Verified exact brand identity'},cookie)[0],200)
        _,product,_=self.request('/api/admin/products',{'brand_id':brand['id'],'name':'Gate Product','version':'2026','format':'Liquid','evidence':['official product page'],'field_evidence':{'version':['official version page'],'format':['official format page']}, 'reason':'Create exact product'},cookie)
        self.assertEqual(self.request('/api/admin/publish-product',{'id':product['id'],'reason':'Verified exact formula and format'},cookie)[0],200)
        made=[]
        for name in ['Reference','Target']:
            _,shade,_=self.request('/api/admin/shades',{'product_id':product['id'],'name':name,'code':name[0], 'depth':'Tan','undertone':'Olive','normalization_state':'Official','depth_confidence':'High','undertone_confidence':'Moderate','field_evidence':{'identity':['official shade chart'],'name':['official shade chart'],'code':['official shade chart'],'depth':['review note 1'],'undertone':['review note 2']},'reason':'Add reviewed shade'},cookie)
            self.assertEqual(self.request('/api/admin/publish-shade',{'id':shade['id'],'reason':'Passed evidence and attribute QA'},cookie)[0],200);made.append(shade['id'])
        _,claim,_=self.request('/api/admin/claims',{'source_shade_id':made[0],'target_shade_id':made[1],'source_wording':'Source-labelled exact — Target','closeness':'Source-labelled exact','confidence':'Moderate','source_file':'reviewed.pdf','source_page':'12','evidence':['reviewed.pdf page 12'],'reason':'Add one-way source claim'},cookie)
        self.assertEqual(self.request('/api/admin/publish-claim',{'id':claim['id'],'reason':'Passed directed claim QA'},cookie)[0],200)
        _,result,_=self.request('/api/matches/known?shade_id='+made[0]);self.assertEqual(len(result['items']),1);self.assertEqual(result['items'][0]['shade_id'],made[1])

if __name__=='__main__':unittest.main()
