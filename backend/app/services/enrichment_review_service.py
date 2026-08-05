"""Multi-source enrichment review foundation: local DB candidates only."""
from __future__ import annotations
import json, sqlite3, uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from ..core.library_root import assert_path_under_root, library_db_path, selected_library_root
from . import analysis_jobs_service, settings_service

_ALLOWED = ('artist', 'title', 'genre')
_DECISIONS = {'pending', 'applied', 'ignored', 'review_later'}
_SAFETY = ['review_first', 'db_only', 'selected_fields_only', 'no_tag_writes', 'no_file_writes', 'no_bpm_key_camelot_cue_changes']
def _now(): return datetime.now(timezone.utc).isoformat()
def _path() -> Path:
    root = selected_library_root(); path = assert_path_under_root(library_db_path(root), root)
    if not path.is_file(): raise ValueError('Configured library is not initialized.')
    return path
def _ensure(conn: sqlite3.Connection):
    conn.execute('CREATE TABLE IF NOT EXISTS enrichment_review_snapshots (id INTEGER PRIMARY KEY, created_at TEXT NOT NULL, items_json TEXT NOT NULL, warnings_json TEXT NOT NULL)')
    conn.execute('CREATE TABLE IF NOT EXISTS enrichment_review_decisions (snapshot_id INTEGER NOT NULL, suggestion_id TEXT NOT NULL, track_id INTEGER NOT NULL, decision TEXT NOT NULL, note TEXT NOT NULL DEFAULT \'\', selected_fields_json TEXT NOT NULL DEFAULT \'{}\', updated_at TEXT NOT NULL, applied_at TEXT, PRIMARY KEY(snapshot_id, suggestion_id))')
    columns = {row[1] for row in conn.execute('PRAGMA table_info(tracks)')}
    for name in ('enrichment_source', 'enrichment_updated_at', 'enrichment_reviewed_at'):
        if name not in columns: conn.execute(f'ALTER TABLE tracks ADD COLUMN {name} TEXT')
def _sources():
    return [{k: source[k] for k in ('id','label','category','enabled','configured','connection_status','current_behavior')} for source in settings_service.get_metadata_sources()['sources']]
def _empty(message: str): return {'summary': {'suggestions':0,'pending':0,'applied':0,'ignored':0,'review_later':0,'fields_selected':0}, 'items':[], 'sources':_sources(), 'safety':_SAFETY, 'warnings':[], 'latest_preview_at':None, 'message':message}
def _valid(fields: Any, allowed: set[str]) -> dict[str,str]:
    if not isinstance(fields, dict): raise ValueError('fields must be an object of explicit selected values.')
    invalid=set(fields)-set(_ALLOWED)
    if invalid: raise ValueError('BPM, key, Camelot, cues, and other unsupported metadata cannot be applied.')
    result={}
    for field,value in fields.items():
        if field not in allowed: raise ValueError(f'{field} is not an empty eligible field for this suggestion.')
        if not isinstance(value,str) or not value.strip() or len(value.strip())>500: raise ValueError(f'{field} must be a non-empty value up to 500 characters.')
        result[field]=value.strip()
    return result
def _latest(conn):
    row=conn.execute('SELECT id,created_at,items_json,warnings_json FROM enrichment_review_snapshots ORDER BY id DESC LIMIT 1').fetchone()
    if not row: raise LookupError('No multi-source preview is saved yet. Refresh preview first.')
    return row
def _response(conn, snapshot):
    items=json.loads(snapshot['items_json']); decisions={row['suggestion_id']:row for row in conn.execute('SELECT * FROM enrichment_review_decisions WHERE snapshot_id=?',(snapshot['id'],))}
    summary={'suggestions':len(items),'pending':0,'applied':0,'ignored':0,'review_later':0,'fields_selected':0}
    for item in items:
        decision=decisions.get(item['suggestion_id']); item['decision']=decision['decision'] if decision else 'pending'; item['note']=decision['note'] if decision else ''; item['selected_fields']=json.loads(decision['selected_fields_json']) if decision else {}
        summary[item['decision']]+=1; summary['fields_selected']+=len(item['selected_fields'])
    return {'summary':summary,'items':items,'sources':_sources(),'safety':_SAFETY,'warnings':json.loads(snapshot['warnings_json']),'latest_preview_at':snapshot['created_at'],'message':'Local and filename-hint suggestions only. External API lookup and Beets subprocess execution are not implemented.'}
def get_review():
    try:
        with sqlite3.connect(_path()) as conn:
            conn.row_factory=sqlite3.Row
            tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
            if 'enrichment_review_snapshots' not in tables:
                return _empty('No multi-source preview is saved. Refresh local suggestions to begin review.')
            try:return _response(conn,_latest(conn))
            except LookupError:return _empty('No multi-source preview is saved. Refresh local suggestions to begin review.')
    except ValueError:return _empty('Initialize and import the local library before refreshing enrichment suggestions.')
def refresh_preview():
    candidates=analysis_jobs_service.beets_enrichment_candidates(); items=[]
    for candidate in candidates:
        current={field:candidate.get(field) for field in _ALLOWED}; missing=[field for field in candidate.get('missing_fields',[]) if field in _ALLOWED]
        if not missing: continue
        filename=str(candidate.get('filename') or 'Track'); suggestion={}
        if ' - ' in filename:
            artist,title=filename.rsplit('.',1)[0].split(' - ',1)
            if 'artist' in missing: suggestion['artist']=artist.strip()
            if 'title' in missing: suggestion['title']=title.strip()
        if not suggestion: continue
        items.append({'suggestion_id':f'sug-{uuid.uuid4().hex[:12]}','track_id':candidate['track_id'],'source_id':'filename_hints','confidence':'low','reason':'Conservative Artist - Title filename hint; review before applying.','filename':filename,'relative_path':candidate.get('relative_path'),'current_fields':current,'suggested_fields':suggestion,'allowed_fields':list(suggestion)})
    warnings=['No external API calls, Beets subprocess execution, tag writes, or file operations were performed.','External sources are settings-only placeholders in this foundation.']
    with sqlite3.connect(_path()) as conn:
        conn.row_factory=sqlite3.Row; _ensure(conn); cursor=conn.execute('INSERT INTO enrichment_review_snapshots(created_at,items_json,warnings_json) VALUES(?,?,?)',(_now(),json.dumps(items),json.dumps(warnings))); snapshot=conn.execute('SELECT id,created_at,items_json,warnings_json FROM enrichment_review_snapshots WHERE id=?',(cursor.lastrowid,)).fetchone(); return _response(conn,snapshot)
def update_suggestion(track_id:int,suggestion_id:str,decision:str,note:str,fields:dict[str,str]):
    if decision not in _DECISIONS: raise ValueError('Invalid review decision.')
    with sqlite3.connect(_path()) as conn:
        conn.row_factory=sqlite3.Row; _ensure(conn); snapshot=_latest(conn); items=json.loads(snapshot['items_json']); item=next((x for x in items if x['suggestion_id']==suggestion_id and x['track_id']==track_id),None)
        if not item: raise LookupError('Suggestion was not found in the latest preview.')
        selected=_valid(fields,set(item['allowed_fields'])); conn.execute('INSERT INTO enrichment_review_decisions(snapshot_id,suggestion_id,track_id,decision,note,selected_fields_json,updated_at) VALUES(?,?,?,?,?,?,?) ON CONFLICT(snapshot_id,suggestion_id) DO UPDATE SET decision=excluded.decision,note=excluded.note,selected_fields_json=excluded.selected_fields_json,updated_at=excluded.updated_at',(snapshot['id'],suggestion_id,track_id,decision,note.strip(),json.dumps(selected),_now())); return _response(conn,snapshot)
def apply_selected(items:list[dict[str,Any]],confirm:bool):
    if not confirm: raise ValueError('Applying enrichment requires confirm=true after review.')
    if not items: raise ValueError('Select at least one saved suggestion.')
    applied=skipped=failed=0; warnings=[]
    with sqlite3.connect(_path()) as conn:
        conn.row_factory=sqlite3.Row; _ensure(conn); snapshot=_latest(conn); snapshot_items={item['suggestion_id']:item for item in json.loads(snapshot['items_json'])}; now=_now()
        for request in items:
            item=snapshot_items.get(request.get('suggestion_id'))
            if not item or item['track_id']!=request.get('track_id'): failed+=1; warnings.append('Suggestion was not found in the latest preview.'); continue
            try: fields=_valid(request.get('fields'),set(item['allowed_fields']))
            except ValueError as exc: failed+=1; warnings.append(str(exc)); continue
            saved=conn.execute('SELECT selected_fields_json FROM enrichment_review_decisions WHERE snapshot_id=? AND suggestion_id=?',(snapshot['id'],item['suggestion_id'])).fetchone()
            if not saved or json.loads(saved['selected_fields_json'])!=fields: failed+=1; warnings.append('Save selected fields before applying.'); continue
            row=conn.execute('SELECT artist,title,genre FROM tracks WHERE id=?',(item['track_id'],)).fetchone()
            if not row: failed+=1; warnings.append('Track no longer exists.'); continue
            if any(row[field] for field in fields): skipped+=1; warnings.append('Existing non-empty metadata is never overwritten.'); continue
            conn.execute(f"UPDATE tracks SET {', '.join(f'{field}=?' for field in fields)}, enrichment_source=?, enrichment_updated_at=?, enrichment_reviewed_at=? WHERE id=?",(*fields.values(),item['source_id'],now,now,item['track_id']))
            conn.execute("UPDATE enrichment_review_decisions SET decision='applied',updated_at=?,applied_at=? WHERE snapshot_id=? AND suggestion_id=?",(now,now,snapshot['id'],item['suggestion_id'])); applied+=1
        review=_response(conn,snapshot)
    return {'applied':applied,'skipped':skipped,'failed':failed,'warnings':warnings,'review':review}
