"""Verified, immutable workspace packages. No credentials or absolute bindings."""
from __future__ import annotations

import json
import shutil
import sqlite3
import tempfile
import uuid
import zipfile
from pathlib import Path

from .catalogue import Catalogue, atomic_json, file_hash, now, safe_relative

FORMAT = "zotibox-database"


def export_workspace(catalogue, destination, include_music=False, progress=lambda *args: None):
    destination = Path(destination).resolve()
    if destination == catalogue.db_path or destination.suffix.lower() != ".zotibox":
        raise ValueError("Choose a .zotibox package destination")
    temporary = destination.with_name(destination.name + "." + str(uuid.uuid4()) + ".tmp")
    try:
        with tempfile.TemporaryDirectory(dir=catalogue.workspace) as scratch:
            snapshot = Path(scratch) / "catalogue.sqlite3"
            with catalogue.connect() as source:
                target = sqlite3.connect(snapshot)
                try:
                    source.backup(target)
                finally:
                    target.close()
            # Absolute output locations belong to the exporting PC.
            clean=sqlite3.connect(snapshot)
            try:
                with clean:
                    clean.execute("DELETE FROM settings WHERE key IN ('playlist_exports','download_folder','rekordbox_path','rekordbox_write_enabled','update_folder')")
                    clean.execute("UPDATE downloads SET output=''")
            finally:
                clean.close()
            manifest = {"format": FORMAT, "version": 1, "created": now(), "includes_music": include_music,
                        "database_sha256": file_hash(snapshot), "music": []}
            with zipfile.ZipFile(temporary, "w", compression=zipfile.ZIP_STORED, allowZip64=True) as archive:
                archive.write(snapshot, "catalogue.sqlite3")
                db = sqlite3.connect(snapshot)
                db.row_factory = sqlite3.Row
                try:
                    rows = db.execute("SELECT * FROM files ORDER BY root_id,relative_path").fetchall()
                finally:
                    db.close()
                if include_music:
                    for index, row in enumerate(rows):
                        if not row["available"]:
                            continue
                        path = catalogue.resolve(row)
                        if path is None or not path.is_file():
                            raise ValueError(f"Music is unavailable: {row['relative_path']}. Remap the folder or export catalogue only.")
                        name = f"music/{row['root_id']}/{row['relative_path']}"
                        import hashlib
                        digest = hashlib.sha256()
                        size = 0
                        with path.open("rb") as source, archive.open(name, "w", force_zip64=True) as target:
                            while block := source.read(1024 * 1024):
                                target.write(block)
                                digest.update(block)
                                size += len(block)
                        if digest.hexdigest() != row["sha256"] or size != row["size"]:
                            raise ValueError(f"Music changed since scanning: {row['relative_path']}. Rescan before exporting.")
                        manifest["music"].append({"name": name, "sha256": row["sha256"], "size": size})
                        progress(index + 1, row["relative_path"])
                archive.writestr("manifest.json", json.dumps(manifest, indent=2))
        temporary.replace(destination)
        return {"files": len(manifest["music"]), "bytes": destination.stat().st_size}
    finally:
        temporary.unlink(missing_ok=True)


def inspect_package(path):
    with zipfile.ZipFile(path) as archive:
        infos = archive.infolist()
        names = [item.filename for item in infos]
        if len(names) != len({name.casefold() for name in names}):
            raise ValueError("Package contains duplicate paths")
        if "manifest.json" not in names or "catalogue.sqlite3" not in names:
            raise ValueError("Not a ZotiBox database package")
        if archive.getinfo("manifest.json").file_size > 16 * 1024 * 1024:
            raise ValueError("Package manifest is too large")
        manifest = json.loads(archive.read("manifest.json"))
        if manifest.get("format") != FORMAT or manifest.get("version") != 1:
            raise ValueError("Unsupported workspace package version")
        expected = {"manifest.json", "catalogue.sqlite3"}
        for music in manifest["music"]:
            relative = safe_relative(music["name"])
            if len(relative.parts) < 3 or relative.parts[0] != "music":
                raise ValueError("Invalid music package path")
            uuid.UUID(relative.parts[1])
            if music["name"] in expected:
                raise ValueError("Duplicate music manifest entry")
            expected.add(music["name"])
            if archive.getinfo(music["name"]).file_size != music["size"]:
                raise ValueError("Music size does not match manifest")
        if set(names) != expected:
            raise ValueError("Package contains unexpected files")
        manifest["transfer_bytes"] = sum(item.file_size for item in infos)
        return manifest


def import_workspace(package, destination, progress=lambda *args: None):
    destination = Path(destination).resolve()
    if destination.exists() and (not destination.is_dir() or any(destination.iterdir())):
        raise ValueError("Import into a new or empty folder; existing workspaces are never overwritten")
    manifest = inspect_package(package)
    destination.parent.mkdir(parents=True, exist_ok=True)
    if shutil.disk_usage(destination.parent).free < manifest["transfer_bytes"] + 16 * 1024 * 1024:
        raise ValueError("Not enough free space for this workspace")
    stage = Path(tempfile.mkdtemp(prefix=".zotibox-import-", dir=destination.parent))
    try:
        with zipfile.ZipFile(package) as archive:
            database = stage / "catalogue.sqlite3"
            with archive.open("catalogue.sqlite3") as source, database.open("wb") as target:
                shutil.copyfileobj(source, target)
            if file_hash(database) != manifest["database_sha256"]:
                raise ValueError("Catalogue checksum failed")
            db = sqlite3.connect(database)
            try:
                db.execute("PRAGMA trusted_schema=OFF")
                if db.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                    raise ValueError("Catalogue integrity check failed")
                if db.execute("PRAGMA foreign_key_check").fetchone():
                    raise ValueError("Catalogue contains broken references")
                if db.execute("SELECT schema_version FROM workspace").fetchone()[0] != 2:
                    raise ValueError("Unsupported catalogue version")
                if db.execute("SELECT 1 FROM sqlite_master WHERE type IN ('trigger','view')").fetchone():
                    raise ValueError("Unexpected catalogue schema")
                roots = [row[0] for row in db.execute("SELECT id FROM roots")]
                for root_id in roots:
                    uuid.UUID(root_id)
                file_rows = db.execute("SELECT root_id,relative_path,sha256,size FROM files").fetchall()
                indexed = {}
                for root_id, relative, digest, size in file_rows:
                    safe_relative(relative)
                    indexed[f"music/{root_id}/{relative}"] = (digest, size)
            finally:
                db.close()
            for index, music in enumerate(manifest["music"]):
                if indexed.get(music["name"]) != (music["sha256"], music["size"]):
                    raise ValueError("Music manifest does not match the catalogue")
                output = stage.joinpath(*safe_relative(music["name"]).parts)
                if not output.resolve().is_relative_to(stage):
                    raise ValueError("Package path escapes workspace")
                output.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(music["name"]) as source, output.open("wb") as target:
                    shutil.copyfileobj(source, target)
                if file_hash(output) != music["sha256"]:
                    raise ValueError(f"Music checksum failed: {music['name']}")
                progress(index + 1, music["name"])
            bindings = {}
            if manifest["includes_music"]:
                for root_id in roots:
                    (stage / "music" / root_id).mkdir(parents=True, exist_ok=True)
                    bindings[root_id] = str(destination / "music" / root_id)
            atomic_json(stage / "machine.json", {"roots": bindings})
            # Validate the whole schema before committing the import.
            imported = Catalogue(stage)
            imported.files()
            imported.playlists()
            # Download execution belongs to the importing PC and never starts from a transfer.
            with imported.connect() as db:
                db.execute("UPDATE downloads SET state='paused',message='Imported queue; review before resuming' WHERE state IN ('queued','running')")
        if destination.exists():
            destination.rmdir()  # Empty directory only, never recursive.
        stage.rename(destination)
        return str(destination)
    finally:
        if stage.exists():
            shutil.rmtree(stage)  # Only the newly created private staging directory.
