from __future__ import annotations
import json, sqlite3
from datetime import datetime, timezone
from fastapi import APIRouter, HTTPException, Path
from pydantic import BaseModel, Field
from ...core.library_root import library_db_path, selected_library_root

router=APIRouter(tags=['genres'])
PREFERRED=['Afro House','Afro Tech','Amapiano','Amapiano Gospel','Afrobeats','Afrobeat','Highlife','Hiplife','Gospel','House','Deep House','Soulful House','Dancehall','Reggae','Hip Hop','R&B','Pop','Electronic','Unknown','Needs Review']
MAPPINGS={'afrohouse':'Afro House','afro house':'Afro House','afro-house':'Afro House','afro tech':'Afro Tech','amapiano':'Amapiano','ampiano':'Amapiano','afrobeats':'Afrobeats','afro beats':'Afrobeats','afrobeat':'Afrobeat','high life':'Highlife','highlife':'Highlife','hiplife':'Hiplife','gospel':'Gospel','deep house':'Deep House','soulful house':'Soulful House'}
def now():return datetime.now(timezone.utc).isoformat()
def db():
 p=library_db_path(selected_library_root())
 if not p.is_file():raise ValueError('Configured library is not initialized.')
 return p
def ensure(c):
 c.execute('CREATE TABLE IF NOT EXISTS genre_taxonomy(id INTEGER PRIMARY KEY,name TEXT UNIQUE NOT NULL,parent_name TEXT,description TEXT,enabled INTEGER NOT NULL DEFAULT 1,sort_order INTEGER NOT NULL,created_at TEXT NOT NULL,updated_at TEXT NOT NULL)')
 c.execute('CREATE TABLE IF NOT EXISTS genre_mappings(id INTEGER PRIMARY KEY,raw_genre TEXT UNIQUE NOT NULL,normalized_genre TEXT NOT NULL,confidence TEXT NOT NULL,source TEXT NOT NULL,enabled INTEGER NOT NULL DEFAULT 1,created_at TEXT NOT NULL,updated_at TEXT NOT NULL)')
 c.execute('CREATE TABLE IF NOT EXISTS genre_review_snapshots(id INTEGER PRIMARY KEY,created_at TEXT NOT NULL,items_json TEXT NOT NULL)')
 c.execute('CREATE TABLE IF NOT EXISTS genre_review_decisions(snapshot_id INTEGER,track_id INTEGER,decision TEXT NOT NULL,note TEXT NOT NULL DEFAULT \'\',updated_at TEXT NOT NULL,applied_at TEXT,PRIMARY KEY(snapshot_id,track_id))')
 cols={r[1] for r in c.execute('PRAGMA table_info(tracks)')}
 for n in ('normalized_genre','genre_source','genre_reviewed_at','genre_updated_at'):
  if n not in cols:c.execute(f'ALTER TABLE tracks ADD COLUMN {n} TEXT')
 for i,name in enumerate(PREFERRED):c.execute('INSERT OR IGNORE INTO genre_taxonomy(name,sort_order,created_at,updated_at) VALUES(?,?,?,?)',(name,i,now(),now()))
 for raw,norm in MAPPINGS.items():c.execute('INSERT OR IGNORE INTO genre_mappings(raw_genre,normalized_genre,confidence,source,created_at,updated_at) VALUES(?,?,?,?,?,?)',(raw,norm,'low' if raw=='ampiano' else 'high','default_taxonomy',now(),now()))
def taxonomy(c):return [dict(r) for r in c.execute('SELECT * FROM genre_taxonomy ORDER BY sort_order,name')]
def mapping(c):return [dict(r) for r in c.execute('SELECT * FROM genre_mappings ORDER BY raw_genre')]
class GenreIn(BaseModel):name:str=Field(min_length=1,max_length=100);parent_name:str|None=None;description:str|None=None;enabled:bool=True;sort_order:int=100
class MappingIn(BaseModel):raw_genre:str=Field(min_length=1,max_length=200);normalized_genre:str=Field(min_length=1,max_length=100);confidence:str='manual';source:str='user_mapping';enabled:bool=True
class DecisionIn(BaseModel):decision:str='pending';note:str=''
class ApplyIn(BaseModel):confirm:bool=False;track_ids:list[int]=Field(default_factory=list)
@router.get('/genres/taxonomy')
def get_taxonomy():
 try:
  with sqlite3.connect(db()) as c:
   c.row_factory=sqlite3.Row
   if not c.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='genre_taxonomy'").fetchone():return {'genres':[{'id':None,'name':name,'enabled':1,'sort_order':i} for i,name in enumerate(PREFERRED)]}
   return {'genres':taxonomy(c)}
 except ValueError:return {'genres':[{'id':None,'name':name,'enabled':1,'sort_order':i} for i,name in enumerate(PREFERRED)]}
@router.post('/genres/taxonomy')
def add_taxonomy(body:GenreIn):
 with sqlite3.connect(db()) as c:
  ensure(c)
  try:c.execute('INSERT INTO genre_taxonomy(name,parent_name,description,enabled,sort_order,created_at,updated_at) VALUES(?,?,?,?,?,?,?)',(body.name.strip(),body.parent_name,body.description,int(body.enabled),body.sort_order,now(),now()))
  except sqlite3.IntegrityError:raise HTTPException(422,'Preferred genre already exists.')
 return get_taxonomy()
@router.get('/genres/mappings')
def get_mappings():
 try:
  with sqlite3.connect(db()) as c:
   c.row_factory=sqlite3.Row
   if not c.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='genre_mappings'").fetchone():return {'mappings':[]}
   return {'mappings':mapping(c)}
 except ValueError:return {'mappings':[]}
@router.post('/genres/mappings')
def add_mapping(body:MappingIn):
 with sqlite3.connect(db()) as c:
  ensure(c); valid={x['name'] for x in taxonomy(c)}
  if body.normalized_genre not in valid:raise HTTPException(422,'Normalized genre must be in the preferred taxonomy.')
  try:c.execute('INSERT INTO genre_mappings(raw_genre,normalized_genre,confidence,source,enabled,created_at,updated_at) VALUES(?,?,?,?,?,?,?)',(body.raw_genre.strip().casefold(),body.normalized_genre,body.confidence,body.source,int(body.enabled),now(),now()))
  except sqlite3.IntegrityError:raise HTTPException(422,'Raw genre mapping already exists.')
 return get_mappings()
@router.get('/genres/review')
def review():
 try:
  with sqlite3.connect(db()) as c:
   c.row_factory=sqlite3.Row
   if not c.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='genre_review_snapshots'").fetchone():return {'summary':{'pending':0,'high_confidence':0,'needs_review':0,'missing_genre':0,'applied':0},'items':[],'safety':['db_only','review_first','no_tag_writes','no_file_writes'],'message':'Refresh preview to review local genre values.'}
   snap=c.execute('SELECT * FROM genre_review_snapshots ORDER BY id DESC LIMIT 1').fetchone()
  if not snap:return {'summary':{'pending':0,'high_confidence':0,'needs_review':0,'missing_genre':0,'applied':0},'items':[],'safety':['db_only','review_first','no_tag_writes','no_file_writes'],'message':'Refresh preview to review local genre values.'}
  items=json.loads(snap['items_json']);return {'summary':{'pending':len(items),'high_confidence':sum(x['confidence']=='high' for x in items),'needs_review':sum(x['suggested_genre']=='Needs Review' for x in items),'missing_genre':sum(not x['raw_genre'] for x in items),'applied':0},'items':items,'safety':['db_only','review_first','no_tag_writes','no_file_writes']}
 except ValueError:return {'summary':{'pending':0,'high_confidence':0,'needs_review':0,'missing_genre':0,'applied':0},'items':[],'safety':['db_only','review_first','no_tag_writes','no_file_writes'],'message':'Initialize the local index before genre review.'}
@router.post('/genres/review/preview-refresh')
def refresh():
 with sqlite3.connect(db()) as c:
  c.row_factory=sqlite3.Row;ensure(c); maps={x['raw_genre']:x for x in mapping(c)};items=[]
  for row in c.execute('SELECT id,title,artist,genre FROM tracks'):
   raw=(row['genre'] or '').strip(); key=raw.casefold(); hit=maps.get(key); suggested=hit['normalized_genre'] if hit else ('Needs Review' if raw else None)
   if suggested:items.append({'track_id':row['id'],'title':row['title'],'artist':row['artist'],'raw_genre':raw,'current_genre':raw,'suggested_genre':suggested,'confidence':hit['confidence'] if hit else 'low','reason':'matched mapping' if hit else 'ambiguous or missing genre; no automatic guess','decision':'pending','note':''})
  c.execute('INSERT INTO genre_review_snapshots(created_at,items_json) VALUES(?,?)',(now(),json.dumps(items)));return {'summary':{'pending':len(items),'high_confidence':sum(x['confidence']=='high' for x in items),'needs_review':sum(x['suggested_genre']=='Needs Review' for x in items),'missing_genre':sum(not x['raw_genre'] for x in items),'applied':0},'items':items,'safety':['db_only','review_first','no_tag_writes','no_file_writes']}
@router.post('/genres/review/apply')
def apply(body:ApplyIn):
 if not body.confirm:raise HTTPException(422,'Apply requires confirm=true.')
 with sqlite3.connect(db()) as c:
  c.row_factory=sqlite3.Row;ensure(c);snap=c.execute('SELECT * FROM genre_review_snapshots ORDER BY id DESC LIMIT 1').fetchone()
  if not snap:raise HTTPException(422,'Refresh preview first.')
  selected={x['track_id']:x for x in json.loads(snap['items_json'])};applied=skipped=0
  for id in body.track_ids:
   item=selected.get(id)
   if not item or item['suggested_genre']=='Needs Review':skipped+=1;continue
   c.execute('UPDATE tracks SET normalized_genre=?,genre_source=?,genre_reviewed_at=?,genre_updated_at=? WHERE id=?',(item['suggested_genre'],'genre_taxonomy',now(),now(),id));applied+=1
  return {'applied':applied,'skipped':skipped,'failed':0,'warnings':['Raw genre is preserved; only normalized_genre was written.']}
