#!/usr/bin/env python3
"""
CrateIQ — Track Rollback CLI

Allows you to undo what the pipeline did to a specific file:
  - Restore original metadata tags
  - Move the file back to its original inbox path (optional)

Usage:
    python3 scripts/rollback.py list [--all]
    python3 scripts/rollback.py info <history_id>
    python3 scripts/rollback.py rollback <history_id> [--dry-run] [--restore-path]

Options:
    list                   Show all rollback-eligible history records
    list --all             Include already-rolled-back records
    info <id>              Show full details of a history record
    rollback <id>          Restore original metadata for that track
    --dry-run              Simulate rollback without making any changes
    --restore-path         Also move the file back to its original inbox path

Safety notes:
  - Rollback NEVER deletes files. It only overwrites tags or moves files.
  - If the target file no longer exists, rollback is skipped with a warning.
  - If the original_path destination is occupied, the move is skipped.
  - All rollback actions are logged to processing_log.txt.
"""
import argparse
import json
import sys
from pathlib import Path

# Bootstrap: make sure djtoolkit root is on the path
sys.path.insert(0, str(Path(__file__).parent.parent))

import db
from modules.textlog import log_action


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _restore_tags(filepath: str, original_meta: dict, dry_run: bool) -> bool:
    """
    Write original_meta tag values back to the file using mutagen easy tags.
    Returns True on success.
    """
    try:
        from mutagen import File as MFile
        audio = MFile(filepath, easy=True)
        if audio is None:
            print(f"  [!] Cannot open {filepath} with mutagen")
            return False

        fields_restored = []
        for field in ("title", "artist", "album", "genre"):
            val = original_meta.get(field)
            if val:
                if not dry_run:
                    try:
                        audio[field] = [val]
                    except Exception:
                        pass  # not all formats support every easy field
                fields_restored.append(f"{field}={val!r}")

        if not dry_run:
            audio.save()

        print(f"  {'[DRY-RUN] ' if dry_run else ''}Tags restored: {', '.join(fields_restored)}")
        return True

    except Exception as exc:
        print(f"  [!] Tag restore failed: {exc}")
        return False


def _move_to_original(current_path: str, original_path: str, dry_run: bool) -> bool:
    """
    Move file from current_path back to original_path.
    Does not overwrite if original_path already exists.
    Returns True on success.
    """
    import shutil

    src  = Path(current_path)
    dest = Path(original_path)

    if not src.exists():
        print(f"  [!] Current file not found: {src}")
        return False

    if dest.exists():
        print(f"  [!] Original path already occupied — skipping move: {dest}")
        return False

    print(f"  {'[DRY-RUN] ' if dry_run else ''}Moving: {src.name} → {dest}")
    if not dry_run:
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(src), str(dest))
    return True


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------
def cmd_list(include_rolled_back: bool) -> None:
    rows = db.get_track_history(include_rolled_back=include_rolled_back)
    if not rows:
        print("No history records found.")
        return

    print(f"{'ID':>5}  {'File':<45}  {'Created':<20}  {'Rolled back'}")
    print(f"{'--':>5}  {'-'*45}  {'-'*20}  {'-'*11}")
    for row in rows:
        fname   = Path(row["filepath"]).name[:45]
        created = row["created_at"][:19].replace("T", " ")
        rb      = "YES" if row["rolled_back"] else "no"
        print(f"{row['id']:>5}  {fname:<45}  {created:<20}  {rb}")


def cmd_info(history_id: int) -> None:
    row = db.get_history_by_id(history_id)
    if row is None:
        print(f"No history record with ID {history_id}")
        return

    print(f"History ID     : {row['id']}")
    print(f"File           : {row['filepath']}")
    print(f"Original path  : {row['original_path'] or '(unknown)'}")
    print(f"Created        : {row['created_at']}")
    print(f"Rolled back    : {'YES — ' + (row['rolled_back_at'] or '') if row['rolled_back'] else 'no'}")
    if row["rollback_note"]:
        print(f"Rollback note  : {row['rollback_note']}")
    print()

    orig = json.loads(row["original_meta"]) if row["original_meta"] else {}
    print("Original metadata:")
    for k, v in orig.items():
        if v:
            print(f"  {k:10}: {v}")

    if row["cleaned_meta"]:
        cleaned = json.loads(row["cleaned_meta"])
        print("\nCleaned metadata (post-sanitization):")
        for k, v in cleaned.items():
            if v and v != orig.get(k):
                print(f"  {k:10}: {v}  (was: {orig.get(k)!r})")

    actions = json.loads(row["actions"]) if row["actions"] else []
    print(f"\nActions performed: {', '.join(actions) or 'none recorded'}")


def cmd_rollback(history_id: int, dry_run: bool, restore_path: bool) -> None:
    row = db.get_history_by_id(history_id)
    if row is None:
        print(f"No history record with ID {history_id}")
        sys.exit(1)

    if row["rolled_back"]:
        print(f"[!] History ID {history_id} was already rolled back on {row['rolled_back_at']}")
        print("    Use --force to roll back again (not currently implemented — check the file manually).")
        sys.exit(1)

    current_path = row["filepath"]
    orig_path    = row["original_path"]

    print(f"{'[DRY-RUN] ' if dry_run else ''}Rolling back history ID {history_id}")
    print(f"  File: {current_path}")

    if not Path(current_path).exists():
        print(f"  [!] File no longer exists — cannot restore tags. Marking as rolled back anyway.")
        if not dry_run:
            db.mark_rolled_back(history_id, note="file not found at rollback time")
            log_action(f"ROLLBACK: ID={history_id} SKIP file not found [{Path(current_path).name}]")
        return

    # Step 1: restore original tags
    orig_meta = json.loads(row["original_meta"]) if row["original_meta"] else {}
    tags_ok   = _restore_tags(current_path, orig_meta, dry_run)

    # Step 2 (optional): move back to original path
    move_ok = True
    if restore_path and orig_path and orig_path != current_path:
        move_ok = _move_to_original(current_path, orig_path, dry_run)
        if move_ok and not dry_run:
            # Update DB to reflect new (old) location
            db.upsert_track(orig_path, status="needs_review")

    # Persist rollback status
    if not dry_run:
        note = "tags restored" + ("; moved to original path" if restore_path and move_ok else "")
        db.mark_rolled_back(history_id, note=note)
        log_action(
            f"ROLLBACK: ID={history_id} {'(dry-run) ' if dry_run else ''}"
            f"tags={'ok' if tags_ok else 'failed'} "
            f"path={'restored' if restore_path and move_ok else 'kept'} "
            f"[{Path(current_path).name}]"
        )

    print(f"  {'[DRY-RUN] ' if dry_run else ''}Done.")


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------
def main() -> None:
    parser = argparse.ArgumentParser(
        description="CrateIQ rollback — restore files to pre-pipeline state",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # list
    p_list = sub.add_parser("list", help="Show history records")
    p_list.add_argument("--all", dest="include_all", action="store_true",
                         help="Include already rolled-back records")

    # info
    p_info = sub.add_parser("info", help="Show details for a history record")
    p_info.add_argument("history_id", type=int)

    # rollback
    p_rb = sub.add_parser("rollback", help="Restore a track to its pre-pipeline state")
    p_rb.add_argument("history_id", type=int)
    p_rb.add_argument("--dry-run", action="store_true",
                       help="Simulate rollback without making any changes")
    p_rb.add_argument("--restore-path", action="store_true",
                       help="Also move the file back to its original inbox path")

    args = parser.parse_args()

    if args.command == "list":
        cmd_list(include_rolled_back=args.include_all)
    elif args.command == "info":
        cmd_info(args.history_id)
    elif args.command == "rollback":
        cmd_rollback(args.history_id, args.dry_run, args.restore_path)


if __name__ == "__main__":
    main()
