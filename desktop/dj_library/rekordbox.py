"""Rekordbox changes are prepared on an isolated copy, then explicitly applied."""
from __future__ import annotations
import json
import os
import shutil
import threading
import uuid
import zipfile
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

import psutil
from .catalogue import atomic_json, file_hash

LOCK=threading.RLock()
SPECIAL={"root","100000","200000"}


def running():
    for process in psutil.process_iter(["name"]):
        if (process.info["name"] or "").lower() in {"rekordbox.exe","rekordbox","rekordboxagent.exe"}:
            return True
    return False


def closed():
    if running():
        raise ValueError("Close Rekordbox and its agent before applying changes or making a complete backup")


def default_database():
    return Path(os.environ.get("APPDATA",""))/"Pioneer"/"rekordbox"/"master.db"


def fingerprint(path):
    return {name:file_hash(path.parent/name) for name in [path.name,path.name+"-wal","masterPlaylists6.xml"] if (path.parent/name).is_file() and (path.parent/name).stat().st_size}


@contextmanager
def database(path):
    from pyrekordbox import Rekordbox6Database
    encrypted=path.read_bytes()[:16]!=b"SQLite format 3\x00"
    db=Rekordbox6Database(path,db_dir=path.parent,unlock=encrypted)
    try:
        yield db
    finally:
        db.close()
        db.engine.dispose()


def consistent_copy(source,destination):
    """SQLCipher read-only connection includes committed WAL data without writing source."""
    from sqlcipher3 import dbapi2 as sql
    from pyrekordbox.db6.database import BLOB
    from pyrekordbox.utils import deobfuscate
    encrypted=source.read_bytes()[:16]!=b"SQLite format 3\x00"
    key=deobfuscate(BLOB) if encrypted else None
    read=sql.connect(source.resolve().as_uri()+"?mode=ro",uri=True)
    write=sql.connect(str(destination))
    try:
        if key:
            read.execute("PRAGMA key='"+key+"'")
            write.execute("PRAGMA key='"+key+"'")
        read.execute("SELECT count(*) FROM sqlite_master").fetchone()
        read.backup(write)
    finally:
        read.close();write.close()


def commit_copy(db):
    # The library's live-process commit guard is unnecessary on this isolated copy.
    db.registry.autoincrement_local_update_count(set_row_usn=True)
    db.session.commit()
    db.registry.clear_buffer()
    if db.playlist_xml:
        for playlist in db.get_playlist():
            item=db.playlist_xml.get(playlist.ID)
            if item and playlist.updated_at and abs((playlist.updated_at-item["Timestamp"]).total_seconds())>1:
                db.playlist_xml.update(playlist.ID,updated_at=playlist.updated_at)
        if db.playlist_xml.modified:
            db.playlist_xml.save()


def name_id(db,table,name):
    if not name.strip():
        return None
    row=db.query(table).filter_by(Name=name.strip()).first()
    if not row:
        row=table.create(ID=db.generate_unused_id(table),UUID=str(uuid.uuid4()),Name=name.strip())
        db.add(row);db.flush()
    return row.ID


class Rekordbox:
    def __init__(self,catalogue):
        self.catalogue=catalogue
        self.root=catalogue.workspace/".local"/"rekordbox"
        self.root.mkdir(parents=True,exist_ok=True)
        self.state_path=self.root/"pending.json"
        self.state=None;self.load_error=None
        try:
            if self.state_path.exists():
                state=json.loads(self.state_path.read_text(encoding="utf-8"))
                if not isinstance(state,dict) or not {"session","source","fingerprint","changes","links"}<=state.keys():raise ValueError("Invalid saved preview")
                self.state=state
        except (OSError,ValueError,TypeError) as exc:self.load_error=str(exc)

    @property
    def source(self):
        return Path(self.catalogue.get_setting("rekordbox_path",str(default_database()))).resolve()

    @property
    def staged(self):
        if not self.state:
            raise ValueError("Connect to Rekordbox first")
        path=self.root/"sessions"/self.state["session"]/"master.db"
        if not path.resolve().is_relative_to((self.root/"sessions").resolve()):
            raise ValueError("Invalid staged session")
        return path

    def connect(self):
        with LOCK:
            if self.state and self.state["changes"]:
                raise ValueError("Apply or discard the pending Rekordbox changes before refreshing")
            source=self.source
            if self.load_error and self.state_path.exists():
                shutil.copy2(self.state_path,self.root/("pending-recovery-"+uuid.uuid4().hex+".json"));self.load_error=None
            if source.name.lower()!="master.db" or not source.is_file():
                raise ValueError("Select the existing Rekordbox master.db file")
            session=str(uuid.uuid4())
            folder=self.root/"sessions"/session;folder.mkdir(parents=True)
            before=fingerprint(source)
            consistent_copy(source,folder/"master.db")
            xml=source.parent/"masterPlaylists6.xml"
            if xml.exists():shutil.copy2(xml,folder/xml.name)
            if fingerprint(source)!=before:
                raise ValueError("Rekordbox changed while connecting; refresh again after closing Rekordbox")
            self.state={"session":session,"source":str(source),"fingerprint":before,"changes":[],"links":{}}
            self.save()
            return self.view()

    def save(self):
        atomic_json(self.state_path,self.state)

    def discard(self):
        with LOCK:
            if self.state:
                self.state["changes"]=[]
            return self.connect()

    def view(self):
        with LOCK,database(self.staged) as db:
            from sqlalchemy.orm import joinedload
            from pyrekordbox.db6.tables import DjmdContent
            tracks=[]
            content_query=db.get_content().options(*(joinedload(getattr(DjmdContent,name)) for name in ["Artist","Album","Genre","Key","Label","Color"]))
            cues={}
            from pyrekordbox.db6.tables import DjmdCue
            for cue in db.session.query(DjmdCue):
                if cue.InMsec is None or cue.InMsec<0:continue
                label=cue.Comment or ("Loop" if cue.Kind==4 else "Load point" if cue.Kind==3 else "Cue / memory point")
                position=cue.InMsec/1000
                cues.setdefault(cue.ContentID,[]).append({"position":position,"end":max(position,(cue.OutMsec or 0)/1000),"label":f"{label} · {int(position)//60}:{int(position)%60:02d}"})
            for content in content_query:
                tracks.append({"id":content.ID,"title":content.Title or "","artist":content.ArtistName or "",
                    "album":content.AlbumName or "","genre":content.GenreName or "","key":content.KeyName or "",
                    "label":content.LabelName or "","rating":round((content.Rating or 0)/51),
                    "color_id":content.ColorID or "","color":content.ColorName or "", "comment":content.Commnt or "",
                    "year":content.ReleaseYear or 0,"track_no":content.TrackNo or 0,
                    "duration":content.Length or 0,"path":content.FolderPath or "","cues":cues.get(content.ID,[])})
            playlists=[{"id":p.ID,"name":p.Name,"parent":p.ParentID,"type":p.Attribute,"seq":p.Seq,
                "songs":[{"id":s.ID,"track_id":s.ContentID,"position":s.TrackNo} for s in sorted(p.Songs,key=lambda s:s.TrackNo)]}
                for p in db.get_playlist() if p.ID not in SPECIAL]
            colors=[{"id":c.ID,"name":c.Commnt or c.ID} for c in db.get_color()]
            return {"tracks":tracks,"playlists":playlists,"colors":colors,"changes":list(self.state["changes"])}

    def stage(self,label,operation):
        with LOCK:
            # Failed operations cannot leave partial modifications in the preview.
            previous=self.staged.parent
            destination=self.root/"sessions"/str(uuid.uuid4())
            destination.mkdir()
            shutil.copy2(self.staged,destination/"master.db")
            xml=previous/"masterPlaylists6.xml"
            if xml.exists():shutil.copy2(xml,destination/xml.name)
            with database(destination/"master.db") as db:
                result=operation(db)
                commit_copy(db)
            self.state["session"]=destination.name
            self.state["changes"].append(label)
            self.save()
            return result

    def playlist(self,db,id_,normal=False):
        row=db.get_playlist(ID=id_)
        if not row or row.ID in SPECIAL or row.Attribute==4 or (normal and row.Attribute!=0):
            raise ValueError("Select an ordinary Rekordbox playlist; smart and system playlists are read-only")
        return row

    def create_playlist(self,name,parent=None,folder=False):
        if not name.strip():raise ValueError("Enter a playlist name")
        def operation(db):
            if parent:
                p=self.playlist(db,parent)
                if p.Attribute!=1:raise ValueError("The parent must be a playlist folder")
            create=db.create_playlist_folder if folder else db.create_playlist
            return create(name.strip(),parent=parent).ID
        return self.stage(f"Create {'folder' if folder else 'playlist'}: {name}",operation)

    def rename_playlist(self,id_,name):
        if not name.strip():raise ValueError("Enter a name")
        return self.stage(f"Rename playlist/folder to {name}",lambda db:db.rename_playlist(self.playlist(db,id_),name.strip()))

    def move_playlist(self,id_,parent=None):
        def operation(db):
            playlist=self.playlist(db,id_)
            if parent:
                folder=self.playlist(db,parent)
                if folder.Attribute!=1:raise ValueError('Drop playlists into a folder')
                ancestor=folder;seen=set()
                while ancestor and ancestor.ID not in SPECIAL:
                    if ancestor.ID==id_ or ancestor.ID in seen:raise ValueError('A folder cannot be moved into itself or its descendants')
                    seen.add(ancestor.ID);ancestor=db.get_playlist(ID=ancestor.ParentID) if ancestor.ParentID else None
            db.move_playlist(playlist,parent=parent or 'root')
            return id_
        return self.stage('Move playlist/folder',operation)

    def delete_playlist(self,id_):
        view=self.view();items={p["id"]:p for p in view["playlists"]}
        if id_ not in items or items[id_]["type"] not in {0,1}:raise ValueError("Select a regular playlist or folder")
        deleted={id_}
        while True:
            children={p["id"] for p in items.values() if p["parent"] in deleted}
            if children<=deleted:break
            deleted|=children
        if deleted&SPECIAL:raise ValueError("System playlists cannot be deleted")
        self.stage(f"Delete {'folder and descendants' if items[id_]['type']==1 else 'playlist'}: {items[id_]['name']}",lambda db:db.delete_playlist(self.playlist(db,id_)))
        self.state.setdefault("deleted_playlists",[]).extend(sorted(deleted))
        for local,target in list(self.state["links"].items()):
            if target in deleted:
                del self.state["links"][local];self.state.get("local_snapshots",{}).pop(local,None)
        self.save()

    def add_tracks(self,id_,tracks):
        def operation(db):
            playlist=self.playlist(db,id_,True)
            for track_id in tracks:
                content=db.get_content(ID=track_id)
                if not content:raise ValueError("Track no longer exists")
                db.add_to_playlist(playlist,content)
        return self.stage(f"Add {len(tracks)} track(s) to playlist",operation)

    def remove_song(self,playlist_id,song_id):
        return self.stage("Remove playlist entry",lambda db:db.remove_from_playlist(self.playlist(db,playlist_id,True),song_id))

    def remove_songs(self,playlist_id,song_ids):
        def operation(db):
            playlist=self.playlist(db,playlist_id,True)
            existing={song.ID for song in playlist.Songs}
            if not set(song_ids)<=existing:raise ValueError("Playlist changed; refresh before removing entries")
            for id_ in dict.fromkeys(song_ids):db.remove_from_playlist(playlist,id_)
        return self.stage(f"Remove {len(song_ids)} playlist entries",operation)

    def move_song(self,playlist_id,song_id,position):
        return self.stage(f"Move playlist entry to position {position}",lambda db:db.move_song_in_playlist(self.playlist(db,playlist_id,True),song_id,position))

    def edit_track(self,id_,values):
        allowed={"title","artist","album","genre","key","label","comment","year","track_no","rating","color_id"}
        if set(values)-allowed:raise ValueError("Unsupported track field")
        if not 0<=int(values.get("rating",0))<=5:raise ValueError("Rating must be 0–5")
        def operation(db):
            from pyrekordbox.db6 import tables
            row=db.get_content(ID=id_)
            if not row:raise ValueError("Track no longer exists")
            for key,column in {"title":"Title","comment":"Commnt","year":"ReleaseYear","track_no":"TrackNo","color_id":"ColorID"}.items():
                if key in values:setattr(row,column,values[key] or None)
            if "rating" in values:row.Rating=int(values["rating"])*51
            if values.get("color_id") and not db.get_color(ID=values["color_id"]):raise ValueError("Unknown Rekordbox colour")
            for key,column,table in [("artist","ArtistID",tables.DjmdArtist),("album","AlbumID",tables.DjmdAlbum),
                    ("genre","GenreID",tables.DjmdGenre),("key","KeyID",tables.DjmdKey),("label","LabelID",tables.DjmdLabel)]:
                if key in values:setattr(row,column,name_id(db,table,values[key]))
        before=next(t for t in self.view()["tracks"] if t["id"]==id_)
        detail="; ".join(f"{key}: {before[key]!s} → {value!s}" for key,value in values.items() if before[key]!=value)
        return self.stage(f"Metadata: {before['title']} — {detail}",operation)

    def sync_local(self,playlist_id,target=None):
        local=next(p for p in self.catalogue.playlists() if p["id"]==playlist_id)
        target=target or (self.state or {}).get("links",{}).get(playlist_id) or local["rekordbox_id"]
        entries=self.catalogue.entries(playlist_id)
        view=self.view()
        if target and not any(p["id"]==target for p in view["playlists"]):target=None
        native_paths={os.path.normcase(os.path.normpath(t["path"])) for t in view["tracks"]}
        files=self.catalogue.files();paths=[];skipped=[]
        for entry in entries:
            choices=[row for row in files if row["recording_id"]==entry["recording_id"] and row["available"]
                      and (path:=self.catalogue.resolve(row)) and path.is_file()] if entry["recording_id"] else []
            choices.sort(key=lambda row:os.path.normcase(os.path.normpath(str(self.catalogue.resolve(row)))) not in native_paths)
            row=choices[0] if choices else None
            path=self.catalogue.resolve(row) if row else None
            if not path or not path.is_file():skipped.append(entry);continue
            paths.append((path,row))
        def operation(db):
            from pyrekordbox.db6 import tables
            playlist=self.playlist(db,target,True) if target else db.create_playlist(local["name"])
            if playlist.Name!=local["name"]:db.rename_playlist(playlist,local["name"])
            existing={}
            for content in db.get_content():existing.setdefault(os.path.normcase(os.path.normpath(content.FolderPath or "")),[]).append(content)
            desired=[]
            for path,row in paths:
                candidates=existing.get(os.path.normcase(os.path.normpath(str(path))),[])
                if len(candidates)>1:raise ValueError("Multiple Rekordbox track IDs share a file path; resolve this ambiguity before synchronising")
                content=candidates[0] if candidates else None
                if not content:
                    content=db.add_content(path,Title=row["title"],Length=int(row["duration"]),
                        ArtistID=name_id(db,tables.DjmdArtist,row["artist"]),AlbumID=name_id(db,tables.DjmdAlbum,row["album"]))
                    existing[os.path.normcase(os.path.normpath(str(path)))]=[content]
                desired.append(content)
            old={}
            for song in sorted(playlist.Songs,key=lambda s:s.TrackNo):old.setdefault(song.ContentID,[]).append(song)
            ordered=[]
            for content in desired:
                queue=old.get(content.ID,[])
                ordered.append(queue.pop(0) if queue else db.add_to_playlist(playlist,content))
            for queue in old.values():
                for song in queue:db.remove_from_playlist(playlist,song)
            for index,song in enumerate(ordered,1):db.move_song_in_playlist(playlist,song,index)
            return playlist.ID
        result=self.stage(f"Sync local playlist {local['name']} ({len(paths)} available entries; {len(skipped)} missing skipped)",operation)
        self.state.setdefault("local_snapshots",{})[playlist_id]={"name":local["name"],"entries":entries,
            "files":[{"path":str(path),"size":row["size"],"mtime_ns":row["mtime_ns"]} for path,row in paths]}
        self.state["links"][playlist_id]=result
        self.state.setdefault("skipped_tracks",{})[playlist_id]=[{"title":e["title"],"artist":e["artist"],"spotify_id":e["spotify_id"]} for e in skipped]
        self.save()
        return result

    def reorder_playlist(self,id_,song_ids):
        def operation(db):
            playlist=self.playlist(db,id_,True)
            songs={s.ID:s for s in playlist.Songs}
            if len(song_ids)!=len(set(song_ids)) or set(song_ids)!=set(songs):raise ValueError("Playlist changed; refresh before reordering")
            for position,song_id in enumerate(song_ids,1):db.move_song_in_playlist(playlist,songs[song_id],position)
        return self.stage("Reorder Rekordbox playlist",operation)

    def add_recordings(self,playlist_id,recording_ids):
        files=self.catalogue.files();paths=[]
        for id_ in recording_ids:
            row=next((r for r in files if r["recording_id"]==id_ and r["available"] and (p:=self.catalogue.resolve(r)) and p.is_file()),None)
            if not row:raise ValueError("Download or match every dragged track before adding to Rekordbox")
            paths.append((self.catalogue.resolve(row),row))
        def operation(db):
            from pyrekordbox.db6 import tables
            playlist=self.playlist(db,playlist_id,True)
            existing={}
            for content in db.get_content():existing.setdefault(os.path.normcase(os.path.normpath(content.FolderPath or "")),[]).append(content)
            for path,row in paths:
                key=os.path.normcase(os.path.normpath(str(path)));matches=existing.get(key,[])
                if len(matches)>1:raise ValueError("Multiple Rekordbox identities share this file; resolve that ambiguity first")
                content=matches[0] if matches else db.add_content(path,Title=row["title"],Length=int(row["duration"]),ArtistID=name_id(db,tables.DjmdArtist,row["artist"]),AlbumID=name_id(db,tables.DjmdAlbum,row["album"]))
                existing[key]=[content];db.add_to_playlist(playlist,content)
        result=self.stage(f"Add {len(paths)} local track(s) to Rekordbox playlist",operation)
        self.state.setdefault("imported_files",[]).extend({"path":str(path),"size":row["size"],"mtime_ns":row["mtime_ns"]} for path,row in paths)
        self.save();return result

    def backups(self):
        return sorted((self.root/"backups").glob("*.zip"),reverse=True)

    def backup(self,progress=lambda *a:None,cancel=None,purpose="manual"):
        with LOCK:
            closed();source=self.source
            if not source.is_file():raise ValueError("Select a Rekordbox database first")
            wal=source.with_name(source.name+"-wal")
            if wal.exists() and wal.stat().st_size:raise ValueError("Open and fully close Rekordbox to checkpoint its journal before backing up")
            folder=self.root/"backups";folder.mkdir(exist_ok=True)
            if folder.resolve().is_relative_to(source.parent.resolve()):raise ValueError("Backups must be stored outside the Rekordbox data directory")
            stamp=datetime.now().strftime("%Y%m%d-%H%M%S")+"-"+uuid.uuid4().hex[:6]
            archive=folder/(stamp+"-"+purpose+".zip")
            before=fingerprint(source)
            manifest={"format":"zotibox-rekordbox-backup-1","files":{},"source":str(source)}
            try:
                with zipfile.ZipFile(archive,"x",zipfile.ZIP_DEFLATED,compresslevel=1) as z:
                    for index,path in enumerate(source.parent.rglob("*")):
                        if cancel and cancel.is_set():raise InterruptedError("Backup cancelled")
                        if path.is_symlink() or path.is_junction():raise ValueError("Rekordbox data directory contains a linked folder; use Rekordbox's native backup")
                        if not path.is_file():continue
                        relative=path.relative_to(source.parent).as_posix()
                        digest=file_hash(path,cancel)
                        z.write(path,"data/"+relative)
                        if file_hash(path,cancel)!=digest:raise ValueError("File changed during backup; retry with Rekordbox closed")
                        manifest["files"][relative]=digest
                        progress(index,relative)
                    z.writestr("manifest.json",json.dumps(manifest))
                if fingerprint(source)!=before:raise ValueError("Rekordbox changed during backup")
                with zipfile.ZipFile(archive) as z:
                    if z.testzip():raise ValueError("Backup verification failed")
                    import hashlib
                    for name,digest in manifest["files"].items():
                        check=hashlib.sha256()
                        with z.open("data/"+name) as stream:
                            while block:=stream.read(1024*1024):
                                if cancel and cancel.is_set():raise InterruptedError("Backup verification cancelled")
                                check.update(block)
                        if check.hexdigest()!=digest:raise ValueError("Backup checksum verification failed")
                return archive
            except Exception:
                archive.unlink(missing_ok=True);raise

    def restore(self,archive,progress=lambda *a:None,cancel=None):
        with LOCK:
            closed()
            if not self.catalogue.get_setting("rekordbox_write_enabled",False):raise ValueError("Enable database writes before restoring")
            if self.state and self.state["changes"]:raise ValueError("Apply or discard pending changes before restoring")
            archive=Path(archive).resolve()
            if archive.parent!=(self.root/"backups").resolve():raise ValueError("Select a managed backup from this database")
            folder=self.root/"restores"/str(uuid.uuid4());folder.mkdir(parents=True)
            from .catalogue import safe_relative
            with zipfile.ZipFile(archive) as z:
                manifest=json.loads(z.read("manifest.json"))
                if manifest.get("format")!="zotibox-rekordbox-backup-1" or manifest.get("source")!=str(self.source):
                    raise ValueError("Backup belongs to a different Rekordbox database")
                names=z.namelist();expected={"manifest.json"}|{"data/"+name for name in manifest["files"]}
                if len(names)!=len(set(n.casefold() for n in names)) or set(names)!=expected:raise ValueError("Invalid backup contents")
                for index,(name,digest) in enumerate(manifest["files"].items()):
                    safe_relative(name)
                    if cancel and cancel.is_set():raise InterruptedError("Restore cancelled")
                    target=folder/name;target.parent.mkdir(parents=True,exist_ok=True)
                    with z.open("data/"+name) as read,target.open("xb") as write:shutil.copyfileobj(read,write)
                    if file_hash(target)!=digest:raise ValueError("Backup checksum failed; nothing restored")
                    progress(index,name)
            if "master.db" not in manifest["files"]:raise ValueError("Backup has no master database")
            self.verify(folder/"master.db")
            rescue=self.backup(progress,cancel,"before-restore")
            before=fingerprint(self.source)
            rollback=self.root/"rollback"/str(uuid.uuid4());rollback.mkdir(parents=True)
            changed=[]
            try:
                closed()
                if fingerprint(self.source)!=before:raise ValueError("Rekordbox changed before restoring")
                for name in manifest["files"]:
                    closed()
                    if cancel and cancel.is_set():raise InterruptedError("Restore cancelled; current library restored")
                    target=self.source.parent/name
                    if target.is_symlink() or any(p.is_symlink() or p.is_junction() for p in target.parents if p.is_relative_to(self.source.parent)):
                        raise ValueError("Restore destination contains a linked folder")
                    target.parent.mkdir(parents=True,exist_ok=True)
                    old=rollback/name if target.exists() else None
                    if old:
                        old.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(target,old)
                    temporary=target.with_name(target.name+".zotibox-"+uuid.uuid4().hex)
                    shutil.copy2(folder/name,temporary)
                    try:os.replace(temporary,target)
                    finally:temporary.unlink(missing_ok=True)
                    changed.append((target,old))
                self.verify(self.source)
            except Exception:
                for target,old in reversed(changed):
                    if old is None:target.unlink(missing_ok=True)
                    else:shutil.copy2(old,target)
                raise
            self.state=None;self.state_path.unlink(missing_ok=True);self.connect()
            return f"Restored backup. Previous state retained in {rescue.name}"

    def apply(self,progress=lambda *a:None,cancel=None):
        with LOCK:
            closed()
            if not self.catalogue.get_setting("rekordbox_write_enabled",False):raise ValueError("Enable Rekordbox database writes in Settings first")
            if not self.state or not self.state["changes"]:raise ValueError("There are no changes to apply")
            for id_,snapshot in self.state.get("local_snapshots",{}).items():
                playlist=next((p for p in self.catalogue.playlists() if p["id"]==id_),None)
                if not playlist or playlist["name"]!=snapshot["name"] or self.catalogue.entries(id_)!=snapshot["entries"]:
                    raise ValueError("A local playlist changed since the preview; prepare its sync again before applying")
                for file in snapshot["files"]:
                    path=Path(file["path"])
                    if not path.is_file() or (path.stat().st_size,path.stat().st_mtime_ns)!=(file["size"],file["mtime_ns"]):
                        raise ValueError("A local music file changed since the preview; scan and prepare the sync again")
            for file in self.state.get("imported_files",[]):
                path=Path(file["path"])
                if not path.is_file() or (path.stat().st_size,path.stat().st_mtime_ns)!=(file["size"],file["mtime_ns"]):
                    raise ValueError("An imported music file changed since the preview; discard and prepare it again")
            wal=self.source.with_name(self.source.name+"-wal")
            if wal.exists() and wal.stat().st_size:
                raise ValueError("Rekordbox has an uncheckpointed journal. Open and fully close Rekordbox, then refresh the preview")
            if str(self.source)!=self.state["source"] or fingerprint(self.source)!=self.state["fingerprint"]:
                raise ValueError("Rekordbox changed since the preview. Discard the preview and refresh before applying")
            self.verify(self.staged)
            backup=self.backup(progress,cancel,"before-write")
            if cancel and cancel.is_set():raise InterruptedError("Apply cancelled; live library unchanged")
            closed()
            if fingerprint(self.source)!=self.state["fingerprint"]:raise ValueError("Rekordbox changed during backup; nothing applied")
            replacements=[(self.staged,self.source)]
            xml=self.staged.parent/"masterPlaylists6.xml"
            if xml.exists():replacements.append((xml,self.source.parent/xml.name))
            previous={target:target.read_bytes() if target.exists() else None for _,target in replacements}
            try:
                for staged,target in replacements:
                    closed()
                    temporary=target.with_name(target.name+".zotibox-"+uuid.uuid4().hex)
                    shutil.copy2(staged,temporary)
                    try:os.replace(temporary,target)
                    finally:temporary.unlink(missing_ok=True)
                self.verify(self.source)
            except Exception:
                for target,data in previous.items():
                    if data is None:target.unlink(missing_ok=True)
                    else:target.write_bytes(data)
                raise
            with self.catalogue.connect() as db:
                for id_ in self.state.get("deleted_playlists",[]):db.execute("UPDATE playlists SET rekordbox_id=NULL WHERE rekordbox_id=?",(id_,))
                for local,target in self.state["links"].items():db.execute("UPDATE playlists SET rekordbox_id=? WHERE id=?",(target,local))
            self.state["changes"]=[];self.connect()
            return f"Applied Rekordbox changes; backup: {backup.name}"

    @staticmethod
    def verify(path):
        with database(path) as db:
            from sqlalchemy import text
            if db.session.execute(text("PRAGMA integrity_check")).scalar()!="ok":raise ValueError("Rekordbox database integrity check failed")
            if db.session.execute(text("PRAGMA foreign_key_check")).first():raise ValueError("Rekordbox database contains broken references")
