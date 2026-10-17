"""Portable identities live in SQLite; absolute root bindings stay on this PC."""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import threading
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath

from mutagen import File as AudioFile

AUDIO_EXTENSIONS = {".mp3", ".flac", ".wav", ".aiff", ".aif", ".m4a", ".aac", ".ogg", ".opus", ".wma", ".alac"}


def uid():
    return str(uuid.uuid4())


def now():
    return datetime.now(timezone.utc).isoformat()


def file_hash(path: Path, cancel=None):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while block := stream.read(1024 * 1024):
            if cancel and cancel.is_set():
                raise InterruptedError("Scan cancelled")
            digest.update(block)
    return digest.hexdigest()


def atomic_json(path, value):
    temporary = path.with_name(path.name+"."+uuid.uuid4().hex+".tmp")
    try:
        temporary.write_text(json.dumps(value, indent=2), encoding="utf-8")
        for attempt in range(5):
            try:temporary.replace(path);break
            except PermissionError:
                if attempt==4:raise
                time.sleep(.025*(attempt+1))
    finally:temporary.unlink(missing_ok=True)


def safe_relative(value):
    path = PurePosixPath(value)
    if not value or path.is_absolute() or ".." in path.parts or "\\" in value or ":" in value or path.as_posix() != value:
        raise ValueError("Invalid relative music path")
    return path


class Catalogue:
    def __init__(self, workspace):
        self.workspace = Path(workspace).resolve()
        self.workspace.mkdir(parents=True, exist_ok=True)
        self.db_path = self.workspace / "catalogue.sqlite3"
        self.bindings_path = self.workspace / "machine.json"
        self.scan_lock = threading.Lock()
        if self.db_path.exists() and self.db_path.stat().st_size:
            existing=sqlite3.connect(self.db_path.as_uri()+"?mode=ro",uri=True)
            try:
                try:
                    version=existing.execute("SELECT schema_version FROM workspace").fetchone()[0]
                except (sqlite3.Error,TypeError) as exc:
                    raise ValueError("This folder does not contain a supported ZotiBox Database") from exc
                if version!=2:
                    raise ValueError(f"Unsupported database version: {version}. Select a fresh ZotiBox Database folder.")
            finally:
                existing.close()
        with self.connect() as db:
            db.executescript("""
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS workspace(id TEXT PRIMARY KEY, schema_version INTEGER NOT NULL);
                CREATE TABLE IF NOT EXISTS roots(id TEXT PRIMARY KEY, name TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS recordings(
                    id TEXT PRIMARY KEY, title TEXT NOT NULL, artist TEXT NOT NULL DEFAULT '',
                    album TEXT NOT NULL DEFAULT '', isrc TEXT NOT NULL DEFAULT '', duration REAL NOT NULL DEFAULT 0,
                    spotify_id TEXT, preferred_file_id TEXT);
                CREATE TABLE IF NOT EXISTS files(
                    id TEXT PRIMARY KEY, recording_id TEXT NOT NULL REFERENCES recordings(id),
                    root_id TEXT NOT NULL REFERENCES roots(id), relative_path TEXT NOT NULL,
                    sha256 TEXT NOT NULL, size INTEGER NOT NULL, mtime_ns INTEGER NOT NULL,
                    codec TEXT NOT NULL, bitrate INTEGER NOT NULL DEFAULT 0, sample_rate INTEGER NOT NULL DEFAULT 0,
                    bits INTEGER NOT NULL DEFAULT 0, available INTEGER NOT NULL DEFAULT 1,
                    created_ns INTEGER NOT NULL DEFAULT 0, indexed_at TEXT NOT NULL DEFAULT '',
                    UNIQUE(root_id, relative_path));
                CREATE INDEX IF NOT EXISTS files_hash ON files(sha256);
                CREATE TABLE IF NOT EXISTS retired_files(sha256 TEXT PRIMARY KEY,recording_id TEXT NOT NULL REFERENCES recordings(id));
                CREATE TABLE IF NOT EXISTS playlists(id TEXT PRIMARY KEY, name TEXT NOT NULL, source TEXT NOT NULL DEFAULT 'manual', spotify_id TEXT,
                    snapshot TEXT, synced_at TEXT, rekordbox_id TEXT);
                CREATE TABLE IF NOT EXISTS remote_tracks(id TEXT PRIMARY KEY, title TEXT NOT NULL, artist TEXT NOT NULL,
                    album TEXT NOT NULL, isrc TEXT NOT NULL, duration REAL NOT NULL, playable INTEGER NOT NULL DEFAULT 1);
                CREATE TABLE IF NOT EXISTS entries(id TEXT PRIMARY KEY, playlist_id TEXT NOT NULL REFERENCES playlists(id) ON DELETE CASCADE,
                    recording_id TEXT REFERENCES recordings(id), position INTEGER NOT NULL,
                    spotify_id TEXT REFERENCES remote_tracks(id), match_state TEXT NOT NULL DEFAULT 'matched');
                CREATE TABLE IF NOT EXISTS matches(spotify_id TEXT PRIMARY KEY REFERENCES remote_tracks(id),
                    recording_id TEXT REFERENCES recordings(id), decision TEXT NOT NULL, evidence TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS downloads(id TEXT PRIMARY KEY, spotify_id TEXT NOT NULL REFERENCES remote_tracks(id),
                    state TEXT NOT NULL DEFAULT 'queued', progress REAL NOT NULL DEFAULT 0, message TEXT NOT NULL DEFAULT '',
                    created TEXT NOT NULL, output TEXT NOT NULL DEFAULT '');
                CREATE TABLE IF NOT EXISTS changes(id TEXT PRIMARY KEY, entity TEXT NOT NULL, entity_id TEXT NOT NULL,
                    action TEXT NOT NULL, timestamp TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS download_sources(download_id TEXT NOT NULL REFERENCES downloads(id) ON DELETE CASCADE,
                    source_key TEXT NOT NULL,name TEXT NOT NULL,position INTEGER NOT NULL DEFAULT 0,PRIMARY KEY(download_id,source_key));
                CREATE TABLE IF NOT EXISTS playlist_folders(id TEXT PRIMARY KEY, name TEXT NOT NULL,
                    parent_id TEXT REFERENCES playlist_folders(id));
                CREATE TABLE IF NOT EXISTS playlist_locations(playlist_id TEXT PRIMARY KEY REFERENCES playlists(id) ON DELETE CASCADE,
                    folder_id TEXT NOT NULL REFERENCES playlist_folders(id));
            """)
            if not db.execute("SELECT 1 FROM workspace").fetchone():
                db.execute("INSERT INTO workspace VALUES (?, 2)", (uid(),))
            version = db.execute("SELECT schema_version FROM workspace").fetchone()[0]
            if version != 2:
                raise ValueError(f"Unsupported workspace version: {version}")
            if 'queue_position' not in {row['name'] for row in db.execute('PRAGMA table_info(downloads)')}:
                db.execute('ALTER TABLE downloads ADD COLUMN queue_position INTEGER NOT NULL DEFAULT 0')
                for position,row in enumerate(db.execute('SELECT id FROM downloads ORDER BY created,id').fetchall()):
                    db.execute('UPDATE downloads SET queue_position=? WHERE id=?',(position,row[0]))
                for row in db.execute('SELECT DISTINCT d.id,p.id playlist_id,p.source,p.spotify_id,p.name,e.position FROM downloads d JOIN entries e ON e.spotify_id=d.spotify_id JOIN playlists p ON p.id=e.playlist_id'):
                    key='spotify:playlist:'+row['spotify_id'] if row['source']=='spotify' else 'local:'+row['playlist_id']
                    db.execute('INSERT OR IGNORE INTO download_sources VALUES (?,?,?,?)',(row['id'],key,row['name'],row['position']))
            if 'finished_at' not in {row['name'] for row in db.execute('PRAGMA table_info(downloads)')}:
                db.execute("ALTER TABLE downloads ADD COLUMN finished_at TEXT NOT NULL DEFAULT ''")
                db.execute("UPDATE downloads SET finished_at=created WHERE state='complete'")
        if not self.bindings_path.exists():
            atomic_json(self.bindings_path, {"roots": {}})

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.db_path, timeout=30)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        db.execute("PRAGMA trusted_schema=OFF")
        try:
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    def bindings(self):
        return json.loads(self.bindings_path.read_text(encoding="utf-8"))["roots"]

    def roots(self):
        bindings = self.bindings()
        with self.connect() as db:
            return [dict(row, path=bindings.get(row["id"], "")) for row in db.execute("SELECT * FROM roots ORDER BY name")]

    def add_root(self, path):
        path = Path(path).resolve(strict=True)
        if not path.is_dir():
            raise ValueError("Select a music folder")
        for root in self.roots():
            if root["path"]:
                existing = Path(root["path"]).resolve()
                if path == existing:
                    return root["id"]
                if path.is_relative_to(existing) or existing.is_relative_to(path):
                    raise ValueError("Music folders must not overlap")
        root_id = uid()
        with self.connect() as db:
            db.execute("INSERT INTO roots VALUES (?,?)", (root_id, path.name or str(path)))
            self.change(db, "root", root_id, "create")
        self.map_root(root_id, path)
        return root_id

    def map_root(self, root_id, path):
        path = Path(path).resolve(strict=True)
        if not path.is_dir():
            raise ValueError("Select a folder")
        with self.connect() as db:
            if not db.execute("SELECT 1 FROM roots WHERE id=?", (root_id,)).fetchone():
                raise ValueError("Unknown music root")
        bindings = self.bindings()
        for other, location in bindings.items():
            existing = Path(location).resolve()
            if other != root_id and (path.is_relative_to(existing) or existing.is_relative_to(path)):
                raise ValueError("Music folders must not overlap")
        bindings[root_id] = str(path)
        atomic_json(self.bindings_path, {"roots": bindings})

    @staticmethod
    def change(db, entity, entity_id, action):
        db.execute("INSERT INTO changes VALUES (?,?,?,?,?)", (uid(), entity, entity_id, action, now()))

    def resolve(self, row):
        binding = self.bindings().get(row["root_id"])
        if not binding:
            return None
        root = Path(binding).resolve()
        result = root.joinpath(*safe_relative(row["relative_path"]).parts).resolve()
        if not result.is_relative_to(root):
            raise ValueError("Music path escapes its root")
        return result

    def scan(self, progress=lambda *args: None, cancel=None, root_ids=None):
        return self._scan(progress, cancel, root_ids)

    def index_file(self, path, root_id, cancel=None):
        return self._scan(lambda *args: None, cancel, {root_id}, Path(path))

    def _scan(self, progress, cancel, root_ids, only_path=None):
        cancel = cancel or threading.Event()
        scan_started=now()
        result = {"indexed": 0, "unchanged": 0, "missing": 0, "errors": []}
        for root in self.roots():
            if root_ids is not None and root["id"] not in root_ids:
                continue
            folder = Path(root["path"]) if root["path"] else None
            if folder is None or not folder.is_dir():
                result["errors"].append(f"{root['name']}: folder is not available; existing records retained")
                continue
            seen = set()
            walk_errors = []
            walk=[(str(only_path.parent), [], [only_path.name])] if only_path else os.walk(folder, followlinks=False, onerror=walk_errors.append)
            for directory, subdirs, names in walk:
                subdirs[:] = [name for name in subdirs if not (Path(directory) / name).is_symlink()
                              and not (Path(directory) / name).is_junction()]
                for name in names:
                    if cancel.is_set():
                        raise InterruptedError("Scan cancelled; indexed files have been saved")
                    path = Path(directory) / name
                    if path.suffix.lower() not in AUDIO_EXTENSIONS or path.is_symlink():
                        continue
                    relative = path.relative_to(folder).as_posix()
                    seen.add(relative)
                    try:
                        with self.scan_lock:
                            stat = path.stat()
                            with self.connect() as db:
                                old = db.execute("SELECT * FROM files WHERE root_id=? AND relative_path=?", (root["id"], relative)).fetchone()
                            if old and old["size"] == stat.st_size and old["mtime_ns"] == stat.st_mtime_ns:
                                with self.connect() as db:
                                    db.execute("UPDATE files SET available=1 WHERE id=?", (old["id"],))
                                result["unchanged"] += 1
                            else:
                                metadata = read_metadata(path)
                                digest = file_hash(path, cancel)
                                after = path.stat()
                                if (after.st_size, after.st_mtime_ns) != (stat.st_size, stat.st_mtime_ns):
                                    raise ValueError("File changed during scan; retry later")
                                with self.connect() as db:
                                    duplicate = db.execute("SELECT recording_id FROM files WHERE sha256=? LIMIT 1", (digest,)).fetchone()
                                    if not duplicate:
                                        duplicate=db.execute("SELECT recording_id FROM retired_files WHERE sha256=?",(digest,)).fetchone()
                                    recording_id = duplicate[0] if duplicate else uid()
                                    if not duplicate:
                                        db.execute("INSERT INTO recordings(id,title,artist,album,isrc,duration) VALUES (?,?,?,?,?,?)",
                                                   (recording_id, metadata["title"], metadata["artist"], metadata["album"], metadata["isrc"], metadata["duration"]))
                                        self.change(db, "recording", recording_id, "create")
                                    file_id = old["id"] if old else uid()
                                    if old:
                                        # A replaced file must not silently substitute a recording in existing playlists.
                                        db.execute("DELETE FROM files WHERE id=?", (file_id,))
                                    db.execute("INSERT INTO files(id,recording_id,root_id,relative_path,sha256,size,mtime_ns,codec,bitrate,sample_rate,bits,available,created_ns,indexed_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,1,?,?)",
                                               (file_id, recording_id, root["id"], relative, digest, stat.st_size, stat.st_mtime_ns,
                                                metadata["codec"], metadata["bitrate"], metadata["sample_rate"], metadata["bits"],
                                                getattr(stat, "st_birthtime_ns", int(stat.st_ctime * 1e9)), old["indexed_at"] if old else now()))
                                    self.change(db, "file", file_id, "index")
                                result["indexed"] += 1
                        progress(result["indexed"] + result["unchanged"], relative)
                    except InterruptedError:
                        raise
                    except Exception as exc:
                        result["errors"].append(f"{relative}: {exc}")
            if only_path:
                continue
            if walk_errors:
                result["errors"].extend(str(exc) for exc in walk_errors)
                continue  # Do not mark inaccessible subtrees missing.
            if cancel.is_set():
                raise InterruptedError("Scan cancelled")
            with self.connect() as db:
                for row in db.execute("SELECT id,relative_path FROM files WHERE root_id=? AND available=1 AND indexed_at<=?", (root["id"],scan_started)).fetchall():
                    if row["relative_path"] not in seen:
                        db.execute("UPDATE files SET available=0 WHERE id=?", (row["id"],))
                        self.change(db, "file", row["id"], "missing")
                        result["missing"] += 1
        return result

    def files(self, query="", duplicates=False):
        with self.connect() as db:
            rows = db.execute("""SELECT f.*,r.title,r.artist,r.album,r.isrc,r.duration,roots.name AS root_name,
                (SELECT count(*) FROM files d WHERE d.sha256=f.sha256) AS copies
                FROM files f JOIN recordings r ON r.id=f.recording_id JOIN roots ON roots.id=f.root_id
                ORDER BY r.artist COLLATE NOCASE,r.title COLLATE NOCASE,f.relative_path""").fetchall()
        needle = query.casefold()
        return [dict(row) for row in rows if (not duplicates or row["copies"] > 1) and
                (not needle or needle in " ".join(str(row[key]) for key in ("title", "artist", "album", "relative_path", "isrc")).casefold())]

    def playlists(self):
        with self.connect() as db:
            return [dict(row) for row in db.execute("SELECT p.*,count(e.id) AS count FROM playlists p LEFT JOIN entries e ON e.playlist_id=p.id GROUP BY p.id ORDER BY p.name COLLATE NOCASE")]

    def create_playlist(self, name):
        if not name.strip():
            raise ValueError("Enter a playlist name")
        playlist_id = uid()
        with self.connect() as db:
            db.execute("INSERT INTO playlists(id,name) VALUES (?,?)", (playlist_id, name.strip()))
            self.change(db, "playlist", playlist_id, "create")
        return playlist_id

    def _manual(self, db, playlist_id):
        row = db.execute("SELECT source FROM playlists WHERE id=?", (playlist_id,)).fetchone()
        if not row or row[0] != "manual":
            raise ValueError("Only independent playlists can be edited here")

    def add_entries(self, playlist_id, recording_ids):
        with self.connect() as db:
            playlist=db.execute('SELECT source FROM playlists WHERE id=?',(playlist_id,)).fetchone()
            if not playlist or playlist[0] not in {'manual','spotify'}:raise ValueError('Select a playlist')
            position = db.execute("SELECT coalesce(max(position),-1)+1 FROM entries WHERE playlist_id=?", (playlist_id,)).fetchone()[0]
            for recording_id in recording_ids:
                if playlist[0]=='spotify' and db.execute('SELECT 1 FROM entries WHERE playlist_id=? AND recording_id=?',(playlist_id,recording_id)).fetchone():continue
                db.execute("INSERT INTO entries(id,playlist_id,recording_id,position) VALUES (?,?,?,?)", (uid(), playlist_id, recording_id, position))
                position += 1
            self.change(db, "playlist", playlist_id, "membership")

    def entries(self, playlist_id):
        with self.connect() as db:
            return [dict(row) for row in db.execute("""SELECT e.*,coalesce(t.title,r.title) AS title,coalesce(t.artist,r.artist) AS artist,
                coalesce(t.duration,r.duration,0) AS duration FROM entries e LEFT JOIN recordings r ON r.id=e.recording_id
                LEFT JOIN remote_tracks t ON t.id=e.spotify_id WHERE playlist_id=? ORDER BY position,e.id""", (playlist_id,))]

    def reorder(self, playlist_id, entry_ids):
        with self.connect() as db:
            playlist=db.execute('SELECT source FROM playlists WHERE id=?',(playlist_id,)).fetchone()
            if not playlist:raise ValueError('Select a playlist')
            existing = {row[0] for row in db.execute("SELECT id FROM entries WHERE playlist_id=?", (playlist_id,))}
            if len(set(entry_ids)) != len(entry_ids) or set(entry_ids) != existing:
                raise ValueError("Playlist changed; refresh before reordering")
            if playlist[0]=='spotify':
                remote=[r[0] for r in db.execute('SELECT id FROM entries WHERE playlist_id=? AND spotify_id IS NOT NULL ORDER BY position,id',(playlist_id,))]
                if entry_ids[:len(remote)]!=remote:raise ValueError('Spotify controls its songs and order. Local extras can be reordered after the Spotify songs.')
            for position, entry_id in enumerate(entry_ids):
                db.execute("UPDATE entries SET position=? WHERE id=?", (position, entry_id))
            self.change(db, "playlist", playlist_id, "order")

    def remove_entry(self, playlist_id, entry_id):
        with self.connect() as db:
            row=db.execute('SELECT p.source,e.spotify_id FROM entries e JOIN playlists p ON p.id=e.playlist_id WHERE e.playlist_id=? AND e.id=?',(playlist_id,entry_id)).fetchone()
            if not row:raise ValueError('Playlist entry no longer exists')
            if row[0]=='spotify' and row[1]:raise ValueError('Remove Spotify songs in Spotify, then sync. Local extras can be removed here.')
            db.execute("DELETE FROM entries WHERE playlist_id=? AND id=?", (playlist_id, entry_id))
            self.change(db, "entry", entry_id, "delete")

    def export_m3u8(self, playlist_id, destination, relative=False):
        destination = Path(destination)
        if destination.suffix.lower() != ".m3u8":
            raise ValueError("Playlist exports must use the .m3u8 extension")
        lines = ["#EXTM3U"]
        missing = []
        for entry in self.entries(playlist_id):
            with self.connect() as db:
                candidates = db.execute("""SELECT f.* FROM files f JOIN recordings r ON r.id=f.recording_id
                    WHERE f.recording_id=? AND f.available=1 ORDER BY (f.id=r.preferred_file_id) DESC,f.id""", (entry["recording_id"],)).fetchall()
            path = next((p for row in candidates if (p := self.resolve(row)) and p.is_file()), None)
            if path is None:
                missing.append(entry["title"])
                continue
            label = f"{entry['artist']} - {entry['title']}".replace("\n", " ").replace("\r", " ")
            location = os.path.relpath(path, destination.parent) if relative and path.drive.lower() == destination.resolve().drive.lower() else str(path)
            lines.extend([f"#EXTINF:{int(entry['duration'])},{label}", location])
        temporary = destination.with_suffix(destination.suffix + ".tmp")
        temporary.write_text("\n".join(lines) + "\n", encoding="utf-8")
        temporary.replace(destination)
        return missing

    def get_setting(self, key, default):
        with self.connect() as db:
            row = db.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
            return json.loads(row[0]) if row else default

    def set_setting(self, key, value):
        with self.connect() as db:
            db.execute("INSERT OR REPLACE INTO settings VALUES (?,?)", (key, json.dumps(value)))

    def remove_root(self, root_id):
        """Remove catalogue references only. Keep recordings/playlist entries and disk files."""
        with self.connect() as db:
            db.execute("INSERT OR IGNORE INTO retired_files SELECT sha256,recording_id FROM files WHERE root_id=?",(root_id,))
            db.execute("DELETE FROM files WHERE root_id=?", (root_id,))
            db.execute("DELETE FROM roots WHERE id=?", (root_id,))
            self.change(db, "root", root_id, "delete")
        bindings = self.bindings()
        bindings.pop(root_id, None)
        atomic_json(self.bindings_path, {"roots": bindings})

    def available_recording(self, recording_id):
        with self.connect() as db:
            rows = db.execute("SELECT * FROM files WHERE recording_id=? AND available=1", (recording_id,)).fetchall()
        return any((path := self.resolve(row)) and path.is_file() for row in rows)

    def remote_track(self, track_id):
        with self.connect() as db:
            row = db.execute("SELECT * FROM remote_tracks WHERE id=?", (track_id,)).fetchone()
            return dict(row) if row else None

    @staticmethod
    def store_track(db, track):
        db.execute("""INSERT INTO remote_tracks VALUES (?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET
            title=excluded.title,artist=excluded.artist,album=excluded.album,isrc=excluded.isrc,
            duration=excluded.duration,playable=excluded.playable""",
            (track["id"], track["title"], track["artist"], track.get("album", ""), track.get("isrc", ""), track.get("duration", 0), int(track.get("playable", True))))

    def save_remote(self, track):
        with self.connect() as db:
            self.store_track(db, track)

    def decide_match(self, spotify_id, recording_id, decision="confirmed", evidence="User selection"):
        with self.connect() as db:
            db.execute("INSERT OR REPLACE INTO matches VALUES (?,?,?,?)", (spotify_id, recording_id, decision, evidence))
            track=db.execute('SELECT playable FROM remote_tracks WHERE id=?',(spotify_id,)).fetchone()
            state='matched' if recording_id else 'unavailable' if track and not track[0] else 'missing'
            db.execute("UPDATE entries SET recording_id=?,match_state=? WHERE spotify_id=?", (recording_id,state,spotify_id))
            self.change(db, "match", spotify_id, decision)

    def playlist_folders(self):
        with self.connect() as db:
            return [dict(row) for row in db.execute('SELECT * FROM playlist_folders ORDER BY name COLLATE NOCASE')]

    def playlist_locations(self):
        with self.connect() as db:
            return dict(db.execute('SELECT playlist_id,folder_id FROM playlist_locations').fetchall())

    def create_playlist_folder(self,name,parent=None):
        if not name.strip():raise ValueError('Enter a folder name')
        id_=uid()
        with self.connect() as db:
            db.execute('INSERT INTO playlist_folders VALUES (?,?,?)',(id_,name.strip(),parent))
        return id_

    def move_playlist_item(self,id_,folder=None,is_folder=False):
        with self.connect() as db:
            folders={row['id']:dict(row) for row in db.execute('SELECT * FROM playlist_folders')}
            if folder is not None and folder not in folders:raise ValueError('The destination folder no longer exists')
            if is_folder:
                if id_ not in folders:raise ValueError('The folder no longer exists')
                ancestor=folder;seen=set()
                while ancestor:
                    if ancestor==id_ or ancestor in seen:raise ValueError('A folder cannot be moved into itself or its descendants')
                    seen.add(ancestor);ancestor=folders[ancestor]['parent_id']
                db.execute('UPDATE playlist_folders SET parent_id=? WHERE id=?',(folder,id_))
            else:
                if not db.execute('SELECT 1 FROM playlists WHERE id=?',(id_,)).fetchone():raise ValueError('The playlist no longer exists')
                db.execute('DELETE FROM playlist_locations WHERE playlist_id=?',(id_,))
                if folder:db.execute('INSERT INTO playlist_locations VALUES (?,?)',(id_,folder))

    def rename_playlist_folder(self,id_,name):
        if not name.strip():raise ValueError('Enter a folder name')
        with self.connect() as db:db.execute('UPDATE playlist_folders SET name=? WHERE id=?',(name.strip(),id_))

    def remove_playlist_folder(self,id_):
        # Removing an organisational folder keeps every playlist and subfolder.
        with self.connect() as db:
            row=db.execute('SELECT parent_id FROM playlist_folders WHERE id=?',(id_,)).fetchone()
            if not row:raise ValueError('The folder no longer exists')
            db.execute('UPDATE playlist_folders SET parent_id=? WHERE parent_id=?',(row[0],id_))
            db.execute('DELETE FROM playlist_locations WHERE folder_id=?',(id_,))
            db.execute('DELETE FROM playlist_folders WHERE id=?',(id_,))

    def sync_playlist(self, snapshot, resolved):
        """Commit only a fully fetched snapshot; membership changes never touch music."""
        if not snapshot.get("complete"):
            raise ValueError("Incomplete Spotify snapshot cannot update a playlist")
        with self.connect() as db:
            existing = db.execute("SELECT id FROM playlists WHERE source='spotify' AND spotify_id=?", (snapshot["id"],)).fetchone()
            playlist_id = existing[0] if existing else uid()
            old = [row[0] for row in db.execute("SELECT spotify_id FROM entries WHERE playlist_id=? AND spotify_id IS NOT NULL ORDER BY position", (playlist_id,))]
            extras=[dict(row) for row in db.execute('SELECT * FROM entries WHERE playlist_id=? AND spotify_id IS NULL ORDER BY position,id',(playlist_id,))]
            if not existing:
                db.execute("INSERT INTO playlists(id,name,source,spotify_id) VALUES (?,?,'spotify',?)", (playlist_id, snapshot["name"], snapshot["id"]))
            db.execute("UPDATE playlists SET name=?,snapshot=?,synced_at=? WHERE id=?", (snapshot["name"], snapshot["snapshot"], now(), playlist_id))
            db.execute("DELETE FROM entries WHERE playlist_id=?", (playlist_id,))
            for position, track in enumerate(snapshot["tracks"]):
                self.store_track(db, track)
                match = resolved.get(track["id"], {"recording_id": None, "state": "missing"})
                db.execute("INSERT INTO entries VALUES (?,?,?,?,?,?)", (uid(), playlist_id, match["recording_id"], position, track["id"], match["state"]))
            # Local-only additions survive every complete remote membership replacement.
            position=len(snapshot['tracks'])
            for extra in extras:
                db.execute('INSERT INTO entries VALUES (?,?,?,?,?,?)',(extra['id'],playlist_id,extra['recording_id'],position,None,extra['match_state']));position+=1
            self.change(db, "playlist", playlist_id, "spotify-snapshot")
        new = [track["id"] for track in snapshot["tracks"]]
        from collections import Counter
        return {"playlist_id": playlist_id, "added": sum((Counter(new) - Counter(old)).values()),
                "removed": sum((Counter(old) - Counter(new)).values()), "tracks": len(new), "order_changed": old != new}

    def detach_playlist(self, playlist_id):
        with self.connect() as db:
            db.execute("UPDATE playlists SET source='manual',spotify_id=NULL,snapshot=NULL WHERE id=?", (playlist_id,))
            self.change(db, "playlist", playlist_id, "detach")

    def rename_playlist(self, playlist_id, name):
        if not name.strip():raise ValueError("Playlist name cannot be empty")
        with self.connect() as db:
            db.execute("UPDATE playlists SET name=? WHERE id=?",(name.strip(),playlist_id))
            self.change(db,"playlist",playlist_id,"rename")

    def remove_file(self, file_id, remove):
        with self.scan_lock:
            row=next((r for r in self.files() if r["id"]==file_id),None)
            if not row:raise ValueError("File is no longer in the catalogue")
            path=self.resolve(row)
            if not path or not path.is_file():raise ValueError("File is missing or unmapped")
            with path.open("rb") as stream:digest=hashlib.file_digest(stream,"sha256").hexdigest()
            if path.stat().st_size!=row["size"] or digest!=row["sha256"]:
                raise ValueError("File changed since scanning; scan again before deleting")
            remove(path)
            with self.connect() as db:
                db.execute("UPDATE files SET available=0 WHERE id=?",(file_id,))
                self.change(db,"file",file_id,"recycle")

    def delete_playlist(self, playlist_id):
        with self.connect() as db:
            db.execute("DELETE FROM playlists WHERE id=?", (playlist_id,))
            self.change(db, "playlist", playlist_id, "delete")

    def enqueue(self, spotify_id,source=None):
        track = self.remote_track(spotify_id)
        if not track or not track["playable"]:
            raise ValueError("This track is unavailable for download")
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            existing = db.execute("SELECT id FROM downloads WHERE spotify_id=? AND state IN ('queued','running','paused')", (spotify_id,)).fetchone()
            if not existing:
                failed=db.execute("SELECT id FROM downloads WHERE spotify_id=? AND state='failed' ORDER BY created DESC,id LIMIT 1",(spotify_id,)).fetchone()
                if failed:
                    existing=failed;db.execute("UPDATE downloads SET state='queued',progress=0,message='Ready to retry',output='' WHERE id=?",(failed[0],))
            job_id=existing[0] if existing else uid()
            if not existing:
                position=db.execute('SELECT COALESCE(max(queue_position),-1)+1 FROM downloads').fetchone()[0]
                db.execute("INSERT INTO downloads(id,spotify_id,created,queue_position) VALUES (?,?,?,?)", (job_id, spotify_id, now(),position))
            sources=[source] if source else [dict(row) for row in db.execute('SELECT p.id,p.source,p.spotify_id,p.name,e.position FROM entries e JOIN playlists p ON p.id=e.playlist_id WHERE e.spotify_id=? ORDER BY p.name,e.position',(spotify_id,))]
            for item in sources:
                key=item.get('key') or ('spotify:playlist:'+item['spotify_id'] if item.get('source')=='spotify' else 'local:'+item['id'])
                db.execute('INSERT OR IGNORE INTO download_sources VALUES (?,?,?,?)',(job_id,key,item['name'],item.get('position',0)))
            return job_id

    def missing_tracks(self):
        with self.connect() as db:
            return [dict(row) for row in db.execute("""SELECT DISTINCT t.* FROM remote_tracks t JOIN entries e ON e.spotify_id=t.id
                WHERE e.match_state='missing' AND t.playable=1 AND NOT EXISTS
                (SELECT 1 FROM downloads d WHERE d.spotify_id=t.id AND d.state IN ('queued','running','paused'))
                ORDER BY t.artist,t.title""")]

    def download_jobs(self):
        with self.connect() as db:
            sources={}
            for row in db.execute("""SELECT s.*,COALESCE(p.name,s.name) display_name FROM download_sources s LEFT JOIN playlists p
                ON s.source_key=CASE WHEN p.source='spotify' THEN 'spotify:playlist:'||p.spotify_id ELSE 'local:'||p.id END ORDER BY s.rowid"""):
                sources.setdefault(row['download_id'],[]).append({'id':row['source_key'],'name':row['display_name'],'position':row['position']})
            rows=[dict(row) for row in db.execute("""SELECT d.*,t.title,t.artist,t.duration FROM downloads d JOIN remote_tracks t ON t.id=d.spotify_id
                ORDER BY CASE d.state WHEN 'running' THEN 0 WHEN 'queued' THEN 1 WHEN 'paused' THEN 1 WHEN 'failed' THEN 1 ELSE 2 END,d.queue_position,d.created,d.id""")]
            for row in rows:
                row['sources']=sources.get(row['id'],[]);row['source_label']=' · '.join(s['name'] for s in row['sources']) or 'Direct downloads'
            return rows

    def claim_next_download(self):
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            if db.execute("SELECT 1 FROM downloads WHERE state IN ('running','removing')").fetchone():return None
            row=db.execute("SELECT id FROM downloads WHERE state IN ('queued','paused') ORDER BY queue_position,created,id LIMIT 1").fetchone()
            if not row:return None
            db.execute("UPDATE downloads SET state='running',progress=0,message='Connecting to Zotify' WHERE id=?",(row[0],))
            id_=row[0]
        return next(row for row in self.download_jobs() if row['id']==id_)

    def remove_downloads(self,ids):
        ids=list(dict.fromkeys(ids));running=[]
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            for id_ in ids:
                row=db.execute('SELECT state FROM downloads WHERE id=?',(id_,)).fetchone()
                if not row:continue
                if row[0] in {'running','removing'}:
                    db.execute("UPDATE downloads SET state='removing',message='Removing' WHERE id=?",(id_,));running.append(id_)
                else:db.execute('DELETE FROM downloads WHERE id=?',(id_,))
        return running

    def finish_queue_removals(self):
        with self.connect() as db:db.execute("DELETE FROM downloads WHERE state='removing'")

    def reorder_downloads(self,ids):
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            pending=[r[0] for r in db.execute("SELECT id FROM downloads WHERE state IN ('queued','paused','failed') ORDER BY queue_position,created,id")]
            if len(ids)!=len(set(ids)) or set(ids)!=set(pending):raise ValueError('The pending queue changed; try the move again')
            for position,id_ in enumerate(ids):db.execute('UPDATE downloads SET queue_position=? WHERE id=?',(position,id_))

    def move_downloads(self,ids,before=None):
        pending=[r['id'] for r in self.download_jobs() if r['state'] in {'queued','paused','failed'}]
        if not ids or not set(ids)<=set(pending):raise ValueError('Select pending tracks; the active download keeps its position')
        moved=[id_ for id_ in pending if id_ in ids];remaining=[id_ for id_ in pending if id_ not in ids]
        if before in moved:return
        target=remaining.index(before) if before in remaining else len(remaining)
        self.reorder_downloads(remaining[:target]+moved+remaining[target:])

    def prioritise_sources(self,keys):
        pending=[r for r in self.download_jobs() if r['state'] in {'queued','paused','failed'}];rank={key:i for i,key in enumerate(keys)}
        def priority(row):return min((rank.get(s['id'],len(rank)) for s in row['sources']),default=rank.get('direct',len(rank)))
        self.reorder_downloads([r['id'] for r in sorted(pending,key=priority)])

    def update_download(self, job_id, state=None, progress=None, message=None, output=None):
        values = {key: value for key, value in {"state": state, "progress": progress, "message": message, "output": output}.items() if value is not None}
        if state=='complete':values['finished_at']=now()
        if values:
            with self.connect() as db:
                db.execute("UPDATE downloads SET " + ",".join(key + "=?" for key in values) + " WHERE id=? AND state!='removing'", (*values.values(), job_id))


def read_metadata(path):
    audio = AudioFile(path, easy=True)
    if audio is None or not getattr(audio, "info", None):
        raise ValueError("Unsupported or unreadable audio file")
    tags = audio.tags or {}
    def tag(key, default=""):
        id3_key = {"title": "TIT2", "artist": "TPE1", "album": "TALB", "isrc": "TSRC"}.get(key)
        value = tags.get(key) or (tags.get(id3_key) if id3_key else None) or [default]
        if hasattr(value, "text"):
            value = value.text
        return str(value[0] if isinstance(value, list) else value)
    info = audio.info
    return {"title": tag("title", path.stem), "artist": tag("artist"), "album": tag("album"),
            "isrc": tag("isrc"), "duration": float(getattr(info, "length", 0)),
            "codec": str(getattr(info, "codec_description", "") or path.suffix.lstrip(".").upper()), "bitrate": int(getattr(info, "bitrate", 0) or 0),
            "sample_rate": int(getattr(info, "sample_rate", 0) or 0), "bits": int(getattr(info, "bits_per_sample", 0) or 0)}
