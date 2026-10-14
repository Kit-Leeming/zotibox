"""Temporary 0.3 maintenance tool; remove its UI in the next feature release."""
from pathlib import Path
from .catalogue import file_hash
from .downloads import safe_name


def rename_plan(catalogue):
    folder = Path(catalogue.get_setting("download_folder", str(catalogue.workspace / "Music"))).resolve()
    plan = []
    occupied = set()
    for row in catalogue.files():
        source = catalogue.resolve(row)
        if not source or not row["available"] or not source.resolve().is_relative_to(folder):
            continue
        target = source.with_name(safe_name(row["artist"] + " - " + row["title"]) + source.suffix.lower())
        key = str(target).casefold()
        status = "Ready"
        if not row["artist"] or not row["title"]:
            status = "Missing artist or title"
        elif source.name == target.name:
            status = "Already named"
        elif target.exists() or key in occupied:
            status = "Name collision; unchanged"
        occupied.add(key)
        plan.append({"file": row, "source": source, "target": target, "status": status})
    return plan


def apply_renames(catalogue, plan):
    changed = 0
    with catalogue.scan_lock:
        for item in plan:
            if item["status"] != "Ready":
                continue
            row, source, target = item["file"], item["source"], item["target"]
            # Recompute boundaries/targets so a stale preview cannot redirect a rename.
            fresh = next((p for p in rename_plan(catalogue) if p["file"]["id"] == row["id"]), None)
            if not fresh or fresh["status"] != "Ready" or fresh["source"] != source or fresh["target"] != target:
                continue
            if source.is_symlink() or source.resolve() != source.absolute() or file_hash(source) != row["sha256"]:
                raise ValueError("File changed after preview; scan and review again")
            root = next(r for r in catalogue.roots() if r["id"] == row["root_id"])
            target_relative = target.relative_to(Path(root["path"])).as_posix()
            source.rename(target)  # Windows refuses an existing destination.
            try:
                with catalogue.connect() as db:
                    db.execute("UPDATE files SET relative_path=? WHERE id=?", (target_relative, row["id"]))
                    db.execute("UPDATE downloads SET output=? WHERE output=?", (str(target), str(source)))
                    catalogue.change(db, "file", row["id"], "rename")
            except Exception:
                target.rename(source)
                raise
            # Preserve the matching lyric sidecar when its destination is free.
            old_lyrics, new_lyrics = source.with_suffix(".lrc"), target.with_suffix(".lrc")
            if old_lyrics.exists() and not new_lyrics.exists():
                old_lyrics.rename(new_lyrics)
            changed += 1
    return changed
