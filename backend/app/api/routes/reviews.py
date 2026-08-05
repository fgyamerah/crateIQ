from __future__ import annotations
import sqlite3
from datetime import datetime,timezone
from fastapi import APIRouter,HTTPException,Path
from pydantic import BaseModel,Field
from ...core.library_root import library_db_path,selected_library_root
router=APIRouter(tags=['reviews']);STATUSES={'unreviewed','reviewed','favorite','maybe','rejected','needs_work'}
def now():return datetime.now(timezone.utc).isoformat()
def db():
 p=library_db_path(selected_library_root())
 if not p.is_file():raise ValueError('Configured library is not initialized.')
 return p
def ensure(c):c.execute("CREATE TABLE IF NOT EXISTS track_reviews(track_id INTEGER PRIMARY KEY,review_status TEXT NOT NULL DEFAULT 'unreviewed',rating INTEGER,notes TEXT NOT NULL DEFAULT '',play_count INTEGER NOT NULL DEFAULT 0,last_played_at TEXT,reviewed_at TEXT,updated_at TEXT NOT NULL)")
class Update(BaseModel):review_status:str|None=None;rating:int|None=Field(default=None,ge=0,le=5);notes:str|None=Field(default=None,max_length=2000)
def item(c,id):
 c.row_factory=sqlite3.Row;r=c.execute("SELECT t.id track_id,t.title,t.artist,t.filename,t.genre,t.bpm,t.key_camelot,t.duration_sec,COALESCE(v.review_status,'unreviewed') review_status,v.rating,COALESCE(v.notes,'') notes,COALESCE(v.play_count,0) play_count,v.last_played_at,v.reviewed_at,v.updated_at FROM tracks t LEFT JOIN track_reviews v ON v.track_id=t.id WHERE t.id=?",(id,)).fetchone()
 if not r:raise LookupError('Track not found.')
 return dict(r)
@router.get('/reviews/tracks')
def list_reviews(status:str|None=None):
 try:
  with sqlite3.connect(db()) as c:
   c.row_factory=sqlite3.Row;has=c.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='track_reviews'").fetchone();sql="SELECT t.id track_id,t.title,t.artist,t.filename,t.genre,t.bpm,t.key_camelot,t.duration_sec," + ("COALESCE(v.review_status,'unreviewed') review_status,v.rating,COALESCE(v.notes,'') notes,COALESCE(v.play_count,0) play_count FROM tracks t LEFT JOIN track_reviews v ON v.track_id=t.id" if has else "'unreviewed' review_status,NULL rating,'' notes,0 play_count FROM tracks t");args=[]
   if status:sql+=" WHERE COALESCE(v.review_status,'unreviewed')=?";args=[status]
   rows=[dict(x) for x in c.execute(sql,args)];summary={s:sum(x['review_status']==s for x in rows) for s in STATUSES};summary['total']=len(rows);return {'items':rows,'summary':summary,'safety':['db_only','no_tag_writes','no_file_writes']}
 except ValueError:return {'items':[],'summary':{'total':0},'safety':['db_only','no_tag_writes','no_file_writes']}
@router.get('/reviews/tracks/{track_id}')
def get_review(track_id:int=Path(ge=1)):
 try:
  with sqlite3.connect(db()) as c:
   ensure(c);result=item(c,track_id);result['safety']=['db_only','no_tag_writes','no_file_writes'];return result
 except LookupError as e:raise HTTPException(404,str(e))
 except ValueError as e:raise HTTPException(422,str(e))
@router.get('/reviews/summary')
def summaries(track_ids:str=''):
 ids=[int(value) for value in track_ids.split(',') if value.strip().isdigit()][:200]
 if not ids:return {'reviews':{},'safety':['db_only','read_only','no_tag_writes','no_file_writes']}
 try:
  with sqlite3.connect(db()) as c:
   c.row_factory=sqlite3.Row; placeholders=','.join('?' for _ in ids); has=c.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='track_reviews'").fetchone(); sql=f"SELECT t.id track_id," + ("COALESCE(r.review_status,'unreviewed') review_status,r.rating FROM tracks t LEFT JOIN track_reviews r ON r.track_id=t.id" if has else "'unreviewed' review_status,NULL rating FROM tracks t") + f" WHERE t.id IN ({placeholders})";rows=c.execute(sql,ids);return {'reviews':{str(row['track_id']):{'review_status':row['review_status'],'rating':row['rating']} for row in rows},'safety':['db_only','read_only','no_tag_writes','no_file_writes']}
 except ValueError:return {'reviews':{},'safety':['db_only','read_only','no_tag_writes','no_file_writes']}
@router.patch('/reviews/tracks/{track_id}')
def update(body:Update,track_id:int=Path(ge=1)):
 if body.review_status is not None and body.review_status not in STATUSES:raise HTTPException(422,'Invalid review status.')
 try:
  with sqlite3.connect(db()) as c:
   ensure(c);item(c,track_id);old=item(c,track_id);status=body.review_status or old['review_status'];rating=body.rating if body.rating is not None else old['rating'];notes=body.notes if body.notes is not None else old['notes'];c.execute('INSERT INTO track_reviews(track_id,review_status,rating,notes,reviewed_at,updated_at) VALUES(?,?,?,?,?,?) ON CONFLICT(track_id) DO UPDATE SET review_status=excluded.review_status,rating=excluded.rating,notes=excluded.notes,reviewed_at=excluded.reviewed_at,updated_at=excluded.updated_at',(track_id,status,rating,notes,now() if status!='unreviewed' else None,now()));return item(c,track_id)
 except LookupError as e:raise HTTPException(404,str(e))
 except ValueError as e:raise HTTPException(422,str(e))
@router.post('/reviews/tracks/{track_id}/played')
def played(track_id:int=Path(ge=1)):
 with sqlite3.connect(db()) as c:
  ensure(c);old=item(c,track_id);c.execute('INSERT INTO track_reviews(track_id,review_status,rating,notes,play_count,last_played_at,updated_at) VALUES(?,?,?,?,?,?,?) ON CONFLICT(track_id) DO UPDATE SET play_count=track_reviews.play_count+1,last_played_at=excluded.last_played_at,updated_at=excluded.updated_at',(track_id,old['review_status'],old['rating'],old['notes'],1,now(),now()));return item(c,track_id)
