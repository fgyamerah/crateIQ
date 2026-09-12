from __future__ import annotations
import sqlite3
from datetime import datetime,timezone
from typing import Literal
from fastapi import APIRouter,HTTPException,Path
from pydantic import BaseModel,ConfigDict,Field,PositiveInt,StrictBool,StrictInt
from ...core.library_root import library_db_path,selected_library_root
from ...services import track_review_service
router=APIRouter(tags=['reviews']);STATUSES={'unreviewed','reviewed','favorite','maybe','rejected','needs_work'}
def now():return datetime.now(timezone.utc).isoformat()
def db():
 p=library_db_path(selected_library_root())
 if not p.is_file():raise ValueError('Configured library is not initialized.')
 return p
def ensure(c):track_review_service.ensure_review_table(c)
class Update(BaseModel):
 model_config=ConfigDict(extra='forbid')
 review_status:str|None=None;rating:StrictInt|None=Field(default=None,ge=0,le=5);favorite:StrictBool|None=None;notes:str|None=Field(default=None,max_length=2000)
class ReviewSignalOperation(BaseModel):
 model_config=ConfigDict(extra='forbid')
 operation:Literal['set','clear'];value:StrictInt|StrictBool|None=None
class BulkSignalRequest(BaseModel):
 model_config=ConfigDict(extra='forbid')
 track_ids:list[PositiveInt]=Field(min_length=1,max_length=200)
 operations:dict[str,ReviewSignalOperation]=Field(min_length=1,max_length=2)
class BulkSignalApplyRequest(BulkSignalRequest):
 confirm:bool=False
def item(c,id):
 c.row_factory=sqlite3.Row;r=c.execute("SELECT t.id track_id,t.title,t.artist,t.filename,t.genre,t.bpm,t.key_camelot,t.duration_sec,COALESCE(v.review_status,'unreviewed') review_status,v.rating,COALESCE(v.favorite,CASE WHEN v.review_status='favorite' THEN 1 ELSE 0 END) favorite,COALESCE(v.notes,'') notes,COALESCE(v.play_count,0) play_count,v.last_played_at,v.reviewed_at,v.updated_at FROM tracks t LEFT JOIN track_reviews v ON v.track_id=t.id WHERE t.id=?",(id,)).fetchone()
 if not r:raise LookupError('Track not found.')
 result=dict(r);result['favorite']=bool(result['favorite']);return result
@router.get('/reviews/tracks')
def list_reviews(status:str|None=None):
 if status and status not in STATUSES:raise HTTPException(422,'Invalid review status.')
 try:
  with sqlite3.connect(db()) as c:
   c.row_factory=sqlite3.Row;columns={str(row[1]) for row in c.execute("PRAGMA table_info(track_reviews)")};has=bool(columns);favorite_expr=("COALESCE(v.favorite,CASE WHEN v.review_status='favorite' THEN 1 ELSE 0 END)" if 'favorite' in columns else "CASE WHEN v.review_status='favorite' THEN 1 ELSE 0 END") if has else '0';sql="SELECT t.id track_id,t.title,t.artist,t.filename,t.genre,t.bpm,t.key_camelot,t.duration_sec,COALESCE(v.review_status,'unreviewed') review_status,v.rating,"+favorite_expr+" favorite,COALESCE(v.notes,'') notes,COALESCE(v.play_count,0) play_count,v.last_played_at,v.reviewed_at,v.updated_at FROM tracks t"+(" LEFT JOIN track_reviews v ON v.track_id=t.id" if has else '');args=[]
   if status:
    if not has:
     if status != 'unreviewed':return {'items':[],'summary':{'total':0},'safety':['db_only','no_tag_writes','no_file_writes']}
    else:sql+=" WHERE COALESCE(v.review_status,'unreviewed')=?";args=[status]
   sql+=" ORDER BY LOWER(COALESCE(t.artist,'')),LOWER(COALESCE(t.title,'')),t.id";rows=[dict(x) for x in c.execute(sql,args)];[x.update(favorite=bool(x['favorite'])) for x in rows];summary={s:sum(x['review_status']==s for x in rows) for s in STATUSES};summary['total']=len(rows);return {'items':rows,'summary':summary,'safety':['db_only','no_tag_writes','no_file_writes']}
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
 try:result=track_review_service.summaries(selected_library_root(),ids)
 except (RuntimeError,ValueError):result={}
 return {'reviews':{str(track_id):value for track_id,value in result.items()},'safety':['db_only','read_only','no_tag_writes','no_file_writes']}
@router.patch('/reviews/tracks/{track_id}')
def update(body:Update,track_id:int=Path(ge=1)):
 if body.review_status is not None and body.review_status not in STATUSES:raise HTTPException(422,'Invalid review status.')
 fields=body.model_fields_set
 try:result=track_review_service.update(selected_library_root(),track_id,review_status=body.review_status if 'review_status' in fields else track_review_service._UNSET,rating=body.rating if 'rating' in fields else track_review_service._UNSET,favorite=body.favorite if 'favorite' in fields else track_review_service._UNSET,notes=body.notes if 'notes' in fields else track_review_service._UNSET)
 except ValueError as e:raise HTTPException(422,str(e))
 if result is None:raise HTTPException(404,'Track not found.')
 return result
@router.post('/reviews/tracks/{track_id}/played')
def played(track_id:int=Path(ge=1)):
 result=track_review_service.item(selected_library_root(),track_id,create=True)
 if result is None:raise HTTPException(404,'Track not found.')
 with sqlite3.connect(db()) as c:
  c.execute('UPDATE track_reviews SET play_count=play_count+1,last_played_at=?,updated_at=? WHERE track_id=?',(now(),now(),track_id));c.commit()
 return track_review_service.item(selected_library_root(),track_id,create=True)

@router.post('/reviews/signals/preview')
def preview_signals(body:BulkSignalRequest):
 try:return track_review_service.bulk_preview(selected_library_root(),body.track_ids,operations={field:op.model_dump() for field,op in body.operations.items()})
 except ValueError as e:raise HTTPException(422,str(e))

@router.post('/reviews/signals/apply')
def apply_signals(body:BulkSignalApplyRequest):
 try:return track_review_service.bulk_apply(selected_library_root(),body.track_ids,operations={field:op.model_dump() for field,op in body.operations.items()},confirm=body.confirm)
 except ValueError as e:raise HTTPException(422,str(e))
