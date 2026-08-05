"""Safe local-index metadata issue snapshots; never repairs tracks."""
from __future__ import annotations
import sqlite3
from datetime import datetime, timezone
from ..core.library_root import library_db_path, selected_library_root
def _now(): return datetime.now(timezone.utc).isoformat()
def _db():
 p=library_db_path(selected_library_root())
 if not p.is_file(): raise ValueError('Configured library is not initialized.')
 return p
def _ensure(c):
 c.execute("CREATE TABLE IF NOT EXISTS metadata_repair_queue(id INTEGER PRIMARY KEY,track_id INTEGER NOT NULL,issue_type TEXT NOT NULL,severity TEXT NOT NULL,status TEXT NOT NULL DEFAULT 'open',title TEXT,artist TEXT,relative_path TEXT,current_value TEXT,suggested_action TEXT NOT NULL,note TEXT NOT NULL DEFAULT '',created_at TEXT NOT NULL,updated_at TEXT NOT NULL,reviewed_at TEXT,UNIQUE(track_id,issue_type))")
def _result(c):
 c.row_factory=sqlite3.Row; items=[dict(x) for x in c.execute('SELECT * FROM metadata_repair_queue ORDER BY CASE severity WHEN "high" THEN 1 WHEN "medium" THEN 2 ELSE 3 END,id')]; summary={k:0 for k in ('total','open','reviewed','ignored','review_later','high','medium','low')}
 for x in items:summary['total']+=1;summary[x['status']]+=1;summary[x['severity']]+=1
 return {'summary':summary,'items':items,'safety':['db_only_review','no_tag_writes','no_file_writes','no_auto_fixes']}
def get():
 try:
  with sqlite3.connect(_db()) as c:
   if not c.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='metadata_repair_queue'").fetchone():return {'summary':{'total':0,'open':0,'reviewed':0,'ignored':0,'review_later':0,'high':0,'medium':0,'low':0},'items':[],'safety':['db_only_review','no_tag_writes','no_file_writes','no_auto_fixes']}
   return _result(c)
 except ValueError:return {'summary':{'total':0,'open':0,'reviewed':0,'ignored':0,'review_later':0,'high':0,'medium':0,'low':0},'items':[],'safety':['db_only_review','no_tag_writes','no_file_writes','no_auto_fixes']}
def refresh():
 with sqlite3.connect(_db()) as c:
  c.row_factory=sqlite3.Row;_ensure(c); cols={x[1] for x in c.execute('PRAGMA table_info(tracks)')}; normalized='normalized_genre' in cols; c.execute('DELETE FROM metadata_repair_queue')
  rows=list(c.execute('SELECT * FROM tracks'))
  seen={}
  for r in rows:
   title=r['title'] or '';artist=r['artist'] or '';genre=r['genre'] or '';filename=r['filename'] or ''; issues=[]
   if not artist.strip() or artist.casefold() in {'unknown','unknown artist'}:issues.append(('missing_artist','high','open_enrichment_review',''))
   if not title.strip() or title.casefold() in {'unknown','untitled'}:issues.append(('missing_title','high','open_enrichment_review',''))
   if not genre.strip():issues.append(('missing_genre','medium','open_genre_taxonomy',''))
   if normalized and genre.strip() and not (r['normalized_genre'] or '').strip():issues.append(('missing_normalized_genre','medium','open_genre_taxonomy',genre))
   if '___' in filename or 'youtube' in filename.casefold() or len(filename)>180 or filename.casefold().startswith('track0'):issues.append(('suspicious_filename','low','manual_review',filename))
   if r['bpm'] is None and not r['bpm_trusted']:issues.append(('unknown_bpm','medium','open_jobs_bpm',''))
   if not (r['key_musical'] or r['key_camelot']) and not r['key_trusted']:issues.append(('unknown_key','medium','open_jobs_key',''))
   key=(artist.strip().casefold(),title.strip().casefold())
   if all(key):seen.setdefault(key,[]).append(r['id'])
   for typ,sev,action,value in issues:c.execute('INSERT OR REPLACE INTO metadata_repair_queue(track_id,issue_type,severity,status,title,artist,relative_path,current_value,suggested_action,note,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',(r['id'],typ,sev,'open',title,artist,r['filepath'],value,action,'',_now(),_now()))
  for ids in seen.values():
   if len(ids)>1:
    for tid in ids:c.execute('INSERT OR REPLACE INTO metadata_repair_queue(track_id,issue_type,severity,status,title,artist,relative_path,current_value,suggested_action,note,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',(tid,'duplicate_title_artist','low','open','','','', 'duplicate-looking title/artist','open_duplicates','',_now(),_now()))
  return _result(c)
def update(item_id:int,status:str,note:str):
 if status not in {'open','reviewed','ignored','review_later'}:raise ValueError('Invalid repair queue status.')
 with sqlite3.connect(_db()) as c:
  _ensure(c);n=c.execute('UPDATE metadata_repair_queue SET status=?,note=?,updated_at=?,reviewed_at=? WHERE id=?',(status,note.strip(),_now(),_now() if status!='open' else None,item_id)).rowcount
  if not n:raise LookupError('Repair item not found.')
  return _result(c)
