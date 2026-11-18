#!/usr/bin/env python3
"""Repeatable, provenance-preserving CSV staging import. Never publishes records."""
from pathlib import Path
import argparse, csv, hashlib, json, sqlite3, uuid, datetime as dt, zipfile, xml.etree.ElementTree as ET, re
from server import ROOT, DB_PATH, init_db, connect

PARSER='teinte-csv-stage/1.0'
CSV_TYPES={
 'Complete_Shade_List.csv':'shade_observation',
 'All_Shade_Match_Claims.csv':'match_claim_observation',
 'Product_Catalogue.csv':'product_candidate',
 'Product_Identity_Notes.csv':'identity_note',
 'Attribute_Notes.csv':'attribute_note',
 'Reviewed_Comparisons.csv':'reviewed_comparison',
 'Source_Index.csv':'source_index'
}
def sha(p):
 h=hashlib.sha256()
 with p.open('rb') as f:
  for b in iter(lambda:f.read(1024*1024),b''):h.update(b)
 return h.hexdigest()
def xlsx_rows(path):
 """Read worksheet cells without interpreting formulas, merged headers or embedded text as instructions."""
 ns={'m':'http://schemas.openxmlformats.org/spreadsheetml/2006/main','r':'http://schemas.openxmlformats.org/officeDocument/2006/relationships','p':'http://schemas.openxmlformats.org/package/2006/relationships'}
 with zipfile.ZipFile(path) as z:
  ss=[]
  if 'xl/sharedStrings.xml' in z.namelist():
   root=ET.fromstring(z.read('xl/sharedStrings.xml'))
   ss=[''.join(t.text or '' for t in si.findall('.//m:t',ns)) for si in root.findall('m:si',ns)]
  wb=ET.fromstring(z.read('xl/workbook.xml')); rel=ET.fromstring(z.read('xl/_rels/workbook.xml.rels'))
  links={x.attrib['Id']:x.attrib['Target'] for x in rel}
  for sh in wb.findall('.//m:sheet',ns):
   name=sh.attrib['name']; target=links[sh.attrib['{'+ns['r']+'}id']].lstrip('/')
   if not target.startswith('xl/'):target='xl/'+target
   sheet=ET.fromstring(z.read(target))
   for row in sheet.findall('.//m:sheetData/m:row',ns):
    cells={}
    for c in row.findall('m:c',ns):
     ref=c.attrib.get('r',''); val=c.find('m:v',ns); inline=c.find('m:is',ns); value=val.text if val is not None else None
     if c.attrib.get('t')=='s' and value is not None:value=ss[int(value)]
     elif c.attrib.get('t')=='inlineStr' and inline is not None:value=''.join(t.text or '' for t in inline.findall('.//m:t',ns))
     if value is not None:cells[ref]=value
    if cells:yield name,int(row.attrib.get('r','0')),cells
def main():
 ap=argparse.ArgumentParser(); ap.add_argument('--source-dir',required=True,type=Path,help='Teinte_Complete_Shade_Catalogue_and_Matching_Database directory'); ap.add_argument('--master-catalogue',type=Path,help='Optional Teinte_Master_Catalogue.xlsx path'); a=ap.parse_args()
 base=a.source_dir.expanduser().resolve(); csvdir=base/'CSV Files'
 if not csvdir.is_dir():raise SystemExit(f'Missing CSV Files directory: {csvdir}')
 init_db(); report={'parser_version':PARSER,'source_directory':str(base),'published_records':0,'staged':{},'workbook_staged_rows':{},'source_files':[],'missing_registered_matrices':[]}
 matrices=sorted((base/'Original Source Matrices').glob('*.pdf'))
 # PDFs and workbooks are immutable evidence inputs registered by checksum, not parsed or published.
 evidence_files=matrices
 for p in evidence_files:
  checksum=sha(p); fid=str(uuid.uuid5(uuid.NAMESPACE_URL,f'{p.name}:{checksum}')); row_count=0
  with connect() as db:
   db.execute('INSERT OR IGNORE INTO source_file(id,filename,sha256,imported_at,parser_version,row_count) VALUES(?,?,?,?,?,?)',(fid,p.name,checksum,dt.datetime.now(dt.timezone.utc).isoformat(timespec='seconds'),PARSER,row_count))
  report['source_files'].append({'filename':p.name,'sha256':checksum,'type':'evidence input','staged_rows':0})
 workbooks=sorted(base.glob('*.xlsx'))
 if a.master_catalogue: workbooks.append(a.master_catalogue.expanduser().resolve())
 for p in dict.fromkeys(workbooks):
  checksum=sha(p);fid=str(uuid.uuid5(uuid.NAMESPACE_URL,f'{p.name}:{checksum}')); imported=dt.datetime.now(dt.timezone.utc).isoformat(timespec='seconds'); n=0
  with connect() as db:
   db.execute('INSERT OR IGNORE INTO source_file(id,filename,sha256,imported_at,parser_version,row_count) VALUES(?,?,?,?,?,?)',(fid,p.name,checksum,imported,PARSER,0))
   for sheet_name,line,cells in xlsx_rows(p):
    n+=1
    rid=str(uuid.uuid5(uuid.NAMESPACE_URL,f'{fid}:workbook_row:{sheet_name}:{line}'))
    payload={'workbook_sheet':sheet_name,'source_row':line,'cells':cells}
    db.execute('INSERT OR IGNORE INTO staged_record(id,source_file_id,record_type,source_row,raw_payload,disposition,created_at) VALUES(?,?,?,?,?,?,?)',(rid,fid,'workbook_row',n,json.dumps(payload,ensure_ascii=False,separators=(',',':')),'unreviewed',imported))
   db.execute('UPDATE source_file SET row_count=? WHERE id=?',(n,fid))
  report['source_files'].append({'filename':p.name,'sha256':checksum,'type':'workbook staging','staged_rows':n})
  report['workbook_staged_rows'][p.name]=n
 # Preserve all known CSV rows as raw JSON and stable source row numbers.
 for filename,rtype in CSV_TYPES.items():
  p=csvdir/filename
  if not p.exists():
   report['staged'][filename]={'error':'missing','rows':0};continue
  checksum=sha(p); fid=str(uuid.uuid5(uuid.NAMESPACE_URL,f'{p.name}:{checksum}')); imported=dt.datetime.now(dt.timezone.utc).isoformat(timespec='seconds')
  with p.open('r',encoding='utf-8-sig',newline='') as f:
   reader=csv.DictReader(f); records=list(reader)
   if not reader.fieldnames: raise SystemExit(f'No CSV header in {filename}')
  with connect() as db:
   db.execute('INSERT OR IGNORE INTO source_file(id,filename,sha256,imported_at,parser_version,row_count) VALUES(?,?,?,?,?,?)',(fid,p.name,checksum,imported,PARSER,len(records)))
   for idx,payload in enumerate(records,start=2):
    rid=str(uuid.uuid5(uuid.NAMESPACE_URL,f'{fid}:{rtype}:{idx}'))
    overflow=payload.pop(None,None)
    if overflow is not None:payload['__overflow_fields__']=overflow
    disposition='needs_attention' if overflow is not None else 'unreviewed'
    reason='CSV row has more values than the source header; original overflow preserved.' if overflow is not None else None
    db.execute('INSERT OR IGNORE INTO staged_record(id,source_file_id,record_type,source_row,raw_payload,disposition,disposition_reason,created_at) VALUES(?,?,?,?,?,?,?,?)',(rid,fid,rtype,idx,json.dumps(payload,ensure_ascii=False,separators=(',',':')),disposition,reason,imported))
  rejected=sum(1 for x in records if '__overflow_fields__' in x)
  report['staged'][filename]={'rows':len(records),'rejected_rows':rejected,'record_type':rtype,'sha256':checksum}
  report['source_files'].append({'filename':p.name,'sha256':checksum,'type':'CSV staging input','staged_rows':len(records)})
 report['missing_registered_matrices']=['Full Shade Matrix-5.pdf'] if not (base/'Original Source Matrices'/'Full Shade Matrix-5.pdf').exists() else []
 out=ROOT/'data'/'import-report.json';out.parent.mkdir(exist_ok=True);out.write_text(json.dumps(report,ensure_ascii=False,indent=2))
 print(json.dumps({'csv_rows':sum(v.get('rows',0) for v in report['staged'].values()),'workbook_rows':sum(report['workbook_staged_rows'].values()),'staged_rows_total':sum(v.get('rows',0) for v in report['staged'].values())+sum(report['workbook_staged_rows'].values()),'files':len(report['source_files']),'missing_registered_matrices':report['missing_registered_matrices'],'published_records':0,'report':str(out)},indent=2))
if __name__=='__main__':main()
