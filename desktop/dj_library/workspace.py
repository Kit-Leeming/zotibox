"""Relocate this version's database as a verified copy; retain the original."""
import json
import shutil
import sqlite3
import tempfile
from pathlib import Path

from .catalogue import Catalogue, atomic_json, file_hash


def relocate(catalogue, destination):
    destination = Path(destination).resolve()
    source = catalogue.workspace
    if destination == source or destination.is_relative_to(source) or source.is_relative_to(destination):
        raise ValueError("Choose a separate destination outside the current database folder")
    if destination.exists() and (not destination.is_dir() or any(destination.iterdir())):
        raise ValueError("The destination must be a new or empty folder")
    destination.parent.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix=".zotibox-relocate-", dir=destination.parent))
    try:
        with catalogue.connect() as database:
            backup = sqlite3.connect(stage / "catalogue.sqlite3")
            try:
                database.backup(backup)
            finally:
                backup.close()
        for path in source.iterdir():
            if path.name in {"catalogue.sqlite3", "catalogue.sqlite3-wal", "catalogue.sqlite3-shm", ".application.lock", "machine.json"}:
                continue
            target = stage / path.name
            if path.is_symlink() or path.is_junction():
                raise ValueError("Database folder contains a link; choose a folder without linked files")
            if path.is_dir():
                # Avoid following nested links during relocation.
                if any(child.is_symlink() or child.is_junction() for child in path.rglob("*")):
                    raise ValueError("Database folder contains linked files")
                shutil.copytree(path, target)
            else:
                shutil.copy2(path, target)
        for original in source.rglob("*"):
            if original.is_file() and original.name not in {"catalogue.sqlite3", "catalogue.sqlite3-wal", "catalogue.sqlite3-shm", ".application.lock", "machine.json"}:
                copied = stage / original.relative_to(source)
                if file_hash(original) != file_hash(copied):
                    raise ValueError(f"Relocation checksum failed: {original.name}")
        bindings = {}
        for root_id, location in catalogue.bindings().items():
            location = Path(location).resolve()
            bindings[root_id] = str(destination / location.relative_to(source)) if location.is_relative_to(source) else str(location)
        atomic_json(stage / "machine.json", {"roots": bindings})
        restored = Catalogue(stage)
        with restored.connect() as db:
            if db.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise ValueError("Relocated database failed verification")
        for key in ["download_folder", "playlist_export_folder"]:
            location = catalogue.get_setting(key, "")
            if location and Path(location).resolve().is_relative_to(source):
                restored.set_setting(key, str(destination / Path(location).resolve().relative_to(source)))
        exports=catalogue.get_setting("playlist_exports",{})
        restored.set_setting("playlist_exports",{key:str(destination/Path(value).resolve().relative_to(source))
            if Path(value).resolve().is_relative_to(source) else value for key,value in exports.items()})
        with restored.connect() as db:
            for row in db.execute("SELECT id,output FROM downloads WHERE output!=''").fetchall():
                output=Path(row["output"]).resolve()
                if output.is_relative_to(source):
                    db.execute("UPDATE downloads SET output=? WHERE id=?",(str(destination/output.relative_to(source)),row["id"]))
        if destination.exists():
            destination.rmdir()
        stage.rename(destination)
        return str(destination)
    finally:
        if stage.exists():
            shutil.rmtree(stage)  # Only the newly created, verified sibling staging directory.
