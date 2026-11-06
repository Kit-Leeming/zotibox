"""Rekordbox changes are prepared on an isolated copy, then explicitly applied."""
from __future__ import annotations
import json
import copy
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
        try:
            if (process.info["name"] or "").lower() in {"rekordbox.exe","rekordbox","rekordboxagent.exe"}:
                return True
        except (psutil.NoSuchProcess,psutil.AccessDenied):
            continue
    return False


def closed():
    if running():
        raise ValueError("Close Rekordbox and its agent before applying changes or making a complete backup")


def default_database():
    return Path(os.environ.get("APPDATA",""))/"Pioneer"/"rekordbox"/"master.db"


def fingerprint(path):
    return {name:file_hash(path.parent/name) for name in [path.name,path.name+"-wal","masterPlaylists6.xml"] if (path.parent/name).is_file() and (path.parent/name).stat().st_size}


def analysis_paths(source,value):
    """Resolve a track's own DAT/EXT pair beneath the native share directory."""
    if not isinstance(value,str) or not value or "\x00" in value:return []
    value=value.replace("\\","/")
    if value.startswith("//") or ":" in value:return []
    relative=Path(value.lstrip("/"))
    if relative.is_absolute() or any(part in {".",".."} for part in relative.parts):return []
    if relative.suffix.upper() not in {".DAT",".EXT"}:return []
    share=Path(source).parent/"share"
    try:
        if share.is_symlink() or share.is_junction():return []
        share=share.resolve()
        selected=(share/relative).resolve()
        if not selected.is_relative_to(share):return []
        result=[]
        for suffix in [".DAT",".EXT"]:
            path=selected.with_suffix(suffix).resolve()
            if path.is_relative_to(share) and path.is_file():result.append(str(path))
        return result
    except (OSError,ValueError):return []


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
            previous=self.state
            self.state={"session":session,"source":str(source),"fingerprint":before,"changes":[],"links":{}}
            try:
                result=self.view();self.save();return result
            except Exception:
                self.state=previous;raise

    def refresh_preserving(self,progress=lambda *a:None,cancel=None):
        """Replay pending edits on a fresh private copy; publish only on success."""
        from .rekordbox_recovery import PreviewConflict,prepare,translate
        with LOCK:
            if not self.state or not self.state['changes']:return self.connect()
            previous=copy.deepcopy(self.state)
            if str(self.source)!=previous['source']:
                raise PreviewConflict('The selected Rekordbox database changed. Your preview is kept; select its original database before refreshing.')
            local_changed=any(not (playlist:=next((p for p in self.catalogue.playlists() if p['id']==id_),None)) or
                playlist['name']!=snapshot['name'] or self.catalogue.entries(id_)!=snapshot['entries']
                for id_,snapshot in previous.get('local_snapshots',{}).items())
            if fingerprint(self.source)==previous['fingerprint'] and not local_changed:return self.view()
            recipes=previous.get('recipes',[])
            if len(recipes)!=len(previous['changes']):
                raise PreviewConflict('This preview contains older edits or file organisation changes that cannot be refreshed automatically. Your preview is kept. Apply it against its original library, or export/review it before discarding.')
            # The candidate never publishes pending.json while replaying. A crash,
            # cancellation or failed edit leaves the original recoverable preview.
            candidate=Rekordbox(self.catalogue)
            candidate.state=None;candidate.load_error=None;candidate.save=lambda:None
            progress(0,'Refreshing Rekordbox and keeping your edits')
            candidate.connect();identities={'playlists':{},'tracks':{},'songs':{}}
            view=candidate.view()
            for index,recipe in enumerate(recipes):
                if cancel and cancel.is_set():raise InterruptedError('Refresh cancelled; your pending changes are kept')
                progress(index,f"Keeping edit {index+1} of {len(recipes)}")
                method,args=prepare(recipe,view,identities)
                count=len(candidate.state.get('recipes',[]))
                try:getattr(candidate,method)(*args)
                except (ValueError,StopIteration) as exc:
                    raise PreviewConflict(f"Could not keep edit {index+1}: {previous['changes'][index]}. {exc}\nYour pending changes are kept; undo the conflicting edit or resolve its source before retrying.") from exc
                generated=candidate.state['recipes'][-1]['created'] if len(candidate.state.get('recipes',[]))>count else {'playlists':[],'tracks':[],'songs':[]}
                generated=copy.deepcopy(generated)
                view=candidate.view()
                generated['tracks']=[{k:t[k] for k in ('id','path')} for t in view['tracks']]
                translate(recipe['created'],generated,identities)
            if cancel and cancel.is_set():raise InterruptedError('Refresh cancelled; your pending changes are kept')
            if fingerprint(self.source)!=candidate.state['fingerprint']:
                raise PreviewConflict('Rekordbox changed again during refresh. Your pending changes are kept; close Rekordbox and retry.')
            self.verify(candidate.staged)
            result=candidate.view()
            result['_identities']=identities
            atomic_json(self.state_path,candidate.state)
            self.state=candidate.state
            return result

    def save(self):
        atomic_json(self.state_path,self.state)

    def discard(self):
        with LOCK:
            previous=copy.deepcopy(self.state)
            try:
                self.state=None
                return self.connect()
            except Exception:
                self.state=previous
                raise

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
                position=cue.InMsec/1000
                kind=cue.Kind
                # Keep unfamiliar slot/loop encodings raw instead of guessing.
                cue_type="memory" if kind==0 else "hot" if isinstance(kind,int) and 1<=kind<=8 else "cue"
                is_loop=(cue.OutMsec or 0)>cue.InMsec
                label=cue.Comment or ("Memory" if cue_type=="memory" else "Hot" if cue_type=="hot" else f"Cue (kind {kind})" if kind is not None else "Cue")+(" loop" if is_loop else " cue" if cue_type!="cue" else "")
                cues.setdefault(cue.ContentID,[]).append({"position":position,"end":max(position,(cue.OutMsec or 0)/1000),"label":f"{label} · {int(position)//60}:{int(position)%60:02d}",
                    "kind":kind,"cue_type":cue_type,"is_loop":is_loop,"color":cue.Color,"color_index":cue.ColorTableIndex})
            for content in content_query:
                tracks.append({"id":content.ID,"title":content.Title or "","artist":content.ArtistName or "",
                    "album":content.AlbumName or "","genre":content.GenreName or "","key":content.KeyName or "",
                    "label":content.LabelName or "","rating":round((content.Rating or 0)/51),
                    "color_id":content.ColorID or "","color":content.ColorName or "", "comment":content.Commnt or "",
                    "year":content.ReleaseYear or 0,"track_no":content.TrackNo or 0,
                    "duration":content.Length or 0,'bpm':(content.BPM or 0)/100,'isrc':content.ISRC or '',"path":content.FolderPath or "","cues":cues.get(content.ID,[]),
                    "analysis_paths":analysis_paths(self.source,content.AnalysisDataPath)})
            playlists=[{"id":p.ID,"name":p.Name,"parent":p.ParentID,"type":p.Attribute,"seq":p.Seq,
                "songs":[{"id":s.ID,"track_id":s.ContentID,"position":s.TrackNo} for s in sorted(p.Songs,key=lambda s:s.TrackNo)]}
                for p in db.get_playlist() if p.ID not in SPECIAL]
            colors=[{"id":c.ID,"name":c.Commnt or c.ID} for c in db.get_color()]
            return {"tracks":tracks,"playlists":playlists,"colors":colors,"changes":list(self.state["changes"])}

    def stage(self,label,operation,recipe=None):
        with LOCK:
            from .rekordbox_recovery import capture,created
            before=self.view() if recipe else None
            record=capture(before,*recipe) if recipe else None
            history=copy.deepcopy({key:value for key,value in self.state.items() if key not in {'history','recipes'}})
            history['recipe_count']=len(self.state.get('recipes',[]))
            # Failed operations cannot leave partial modifications in the preview.
            previous=self.staged.parent
            destination=self.root/"sessions"/str(uuid.uuid4())
            destination.mkdir()
            shutil.copy2(self.staged,destination/"master.db")
            xml=previous/"masterPlaylists6.xml"
            if xml.exists():shutil.copy2(xml,destination/xml.name)
            overlays=previous/'native-files'
            if overlays.exists():shutil.copytree(overlays,destination/'native-files')
            with database(destination/"master.db") as db:
                # Some pyrekordbox editing helpers call commit internally. Their
                # live-process guard applies to the native DB, not this copy.
                db.commit=lambda autoinc=True:commit_copy(db)
                result=operation(db)
                commit_copy(db)
            previous=copy.deepcopy(self.state)
            try:
                self.state["session"]=destination.name
                self.state["changes"].append(label)
                self.state.setdefault('history',[]).append(history)
                if record is not None:
                    record['created']=created(before,self.view())
                    self.state.setdefault('recipes',[]).append(record)
                self.save()
            except Exception:
                self.state=previous;raise
            return result

    def undo(self):
        """Restore the last successful private preview without touching Rekordbox."""
        with LOCK:
            if not self.state or not self.state.get('history'):raise ValueError('There is no preview edit to undo')
            history=self.state['history'];previous=copy.deepcopy(history[-1])
            count=previous.pop('recipe_count')
            previous['recipes']=copy.deepcopy(self.state.get('recipes',[])[:count])
            previous['history']=copy.deepcopy(history[:-1])
            current=self.state
            try:
                self.state=previous;result=self.view();self.save();return result
            except Exception:
                self.state=current;raise

    def relocate_files(self,mapping,merge=False,replace_audio=False):
        """Prepare path/identity changes on the preview, including ANLZ PPTH.

        Duplicate identities with competing analysis are deliberately refused.
        No source music, analysis file or live database is modified here.
        """
        from .catalogue import read_metadata
        from pyrekordbox.anlz import AnlzFile
        from sqlalchemy import inspect as sql_inspect
        from pyrekordbox.db6 import tables
        key=lambda p:os.path.normcase(os.path.normpath(str(p)))
        mapping={key(a):Path(b).resolve(strict=True) for a,b in mapping.items() if key(a)!=key(b)}
        guards=[];analysis_guards={};merged={}
        def operation(db):
            table_names=set(sql_inspect(db.engine).get_table_names())
            def has_analysis(content):
                return bool(content.AnalysisDataPath or db.query(tables.DjmdCue).filter_by(ContentID=content.ID).first()
                    or db.query(tables.ContentCue).filter_by(ContentID=content.ID).filter(tables.ContentCue.Cues.is_not(None)).first())
            contents=list(db.get_content());by_path={}
            for content in contents:by_path.setdefault(key(content.FolderPath or ''),[]).append(content)
            targets={}
            for old,target in mapping.items():
                for content in by_path.get(old,[]):targets.setdefault(str(target),[]).append(content)
            for target,selected in targets.items():
                destination=Path(target)
                stat=destination.stat();guards.append({'path':target,'size':stat.st_size,'mtime_ns':stat.st_mtime_ns})
                candidates=list({c.ID:c for c in selected+by_path.get(key(target),[])}.values()) if merge else selected
                if replace_audio:
                    existing=by_path.get(key(target),[])
                    if len(existing)>1:raise ValueError('Resolve duplicate Rekordbox identities for the preferred file first')
                    metadata=read_metadata(destination)
                    keeper=existing[0] if existing else db.add_content(destination,Title=metadata['title'],Length=int(metadata['duration']),
                        ArtistID=name_id(db,tables.DjmdArtist,metadata['artist']),AlbumID=name_id(db,tables.DjmdAlbum,metadata['album']))
                    candidates=[keeper]+[c for c in candidates if c.ID!=keeper.ID]
                if not merge and len(candidates)>1:raise ValueError('Multiple Rekordbox identities share a source path. Use duplicate review first.')
                if not replace_audio:candidates.sort(key=lambda c:not has_analysis(c))
                keeper=candidates[0]
                for loser in candidates[1:]:
                    if db.query(tables.DjmdSongHotCueBanklist).filter_by(ContentID=loser.ID).first():
                        raise ValueError('A duplicate is used in a Rekordbox hot-cue bank. Resolve that bank entry in Rekordbox before merging this identity.')
                    if not replace_audio and has_analysis(loser):
                        raise ValueError('Both copies have Rekordbox analysis or cues. Keep their identities separate and resolve these cues in Rekordbox before merging.')
                    # Rewrite every declared reference, including UUID-based cue
                    # metadata, rather than just the visible playlist songs.
                    for mapper in tables.Base.registry.mappers:
                        model=mapper.class_
                        if model is tables.DjmdContent:continue
                        if mapper.local_table.name not in table_names:continue
                        for column in mapper.local_table.columns:
                            for fk in column.foreign_keys:
                                if fk.column.table.name!='djmdContent':continue
                                attr=fk.column.name
                                for row in db.query(model).filter(column==getattr(loser,attr)).all():
                                    if replace_audio and model in {tables.DjmdCue,tables.ContentCue,tables.ContentActiveCensor,tables.ContentFile}:
                                        if row not in db.session.deleted:db.delete(row)
                                    else:setattr(row,column.name,getattr(keeper,attr))
                    db.flush();db.delete(loser);db.flush();merged[loser.ID]=keeper.ID
                if len(candidates)>1:
                    memberships={}
                    for song in db.query(tables.DjmdSongPlaylist).filter_by(ContentID=keeper.ID).order_by(tables.DjmdSongPlaylist.TrackNo):
                        memberships.setdefault(song.PlaylistID,[]).append(song)
                    for playlist_id,songs in memberships.items():
                        playlist=db.get_playlist(ID=playlist_id)
                        for duplicate in songs[1:]:db.remove_from_playlist(playlist,duplicate)
                old_path=keeper.FolderPath
                # Upstream update_content_path writes ANLZ eagerly. Use isolated
                # overlays so preview/discard cannot touch live analysis.
                for value in analysis_paths(self.source,keeper.AnalysisDataPath):
                    original=Path(value);relative=original.relative_to(self.source.parent)
                    overlay=db.db_directory/'native-files'/relative
                    prior=self.staged.parent/'native-files'/relative
                    overlay.parent.mkdir(parents=True,exist_ok=True)
                    anlz=AnlzFile.parse_file(prior if prior.exists() else original)
                    anlz.set_path(target.replace('\\','/'));anlz.save(overlay)
                    analysis_guards[relative.as_posix()]=file_hash(original)
                keeper.FolderPath=target.replace('\\','/')
                if keeper.OrgFolderPath==old_path:keeper.OrgFolderPath=keeper.FolderPath
                keeper.FileNameL=destination.name;keeper.FileSize=stat.st_size
                for content_file in db.query(tables.ContentFile).filter_by(ContentID=keeper.ID):
                    for name in ('Path','rb_local_path'):
                        if getattr(content_file,name) and key(getattr(content_file,name))==key(old_path):setattr(content_file,name,keeper.FolderPath)
                    content_file.Size=stat.st_size;content_file.rb_file_hash_dirty=1
                metadata=read_metadata(destination)
                keeper.BitRate=metadata['bitrate']//1000;keeper.SampleRate=metadata['sample_rate']
                # Never carry an Ogg file type over to the new MP3 location.
                types={'.mp3':'MP3','.flac':'FLAC','.wav':'WAV','.aif':'AIFF','.aiff':'AIFF','.m4a':'M4A'}
                enum=getattr(tables.FileType,types.get(destination.suffix.lower(),''),None)
                if enum is not None:keeper.FileType=int(enum)
            return len(targets)
        count=self.stage(f'Relink {len(mapping)} music path(s)'+(' and consolidate duplicate identities' if merge else ''),operation)
        self.state.setdefault('imported_files',[]).extend(guards)
        self.state.setdefault('analysis_originals',{}).update(analysis_guards)
        self.state.setdefault('merged_tracks',{}).update(merged)
        self.save();return count

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
        return self.stage(f"Create {'folder' if folder else 'playlist'}: {name}",operation,('create_playlist',[name,parent,folder]))

    def rename_playlist(self,id_,name):
        if not name.strip():raise ValueError("Enter a name")
        return self.stage(f"Rename playlist/folder to {name}",lambda db:db.rename_playlist(self.playlist(db,id_),name.strip()),('rename_playlist',[id_,name]))

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
        return self.stage('Move playlist/folder',operation,('move_playlist',[id_,parent]))

    def delete_playlist(self,id_):
        view=self.view();items={p["id"]:p for p in view["playlists"]}
        if id_ not in items or items[id_]["type"] not in {0,1}:raise ValueError("Select a regular playlist or folder")
        deleted={id_}
        while True:
            children={p["id"] for p in items.values() if p["parent"] in deleted}
            if children<=deleted:break
            deleted|=children
        if deleted&SPECIAL:raise ValueError("System playlists cannot be deleted")
        self.stage(f"Delete {'folder and descendants' if items[id_]['type']==1 else 'playlist'}: {items[id_]['name']}",lambda db:db.delete_playlist(self.playlist(db,id_)),('delete_playlist',[id_]))
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
        return self.stage(f"Add {len(tracks)} track(s) to playlist",operation,('add_tracks',[id_,list(tracks)]))

    def delete_playlists(self,ids):
        import copy
        items={p['id']:p for p in self.view()['playlists']};ids=set(ids)
        if not ids or any(i not in items or items[i]['type'] not in {0,1} for i in ids):raise ValueError('Select regular playlists or folders')
        roots=[]
        for id_ in ids:
            parent=items[id_]['parent'];ancestors=set()
            while parent in items and parent not in ancestors:ancestors.add(parent);parent=items[parent]['parent']
            if not ancestors&ids:roots.append(id_)
        previous=copy.deepcopy(self.state)
        try:
            for id_ in roots:self.delete_playlist(id_)
        except Exception:self.state=previous;self.save();raise

    def remove_song(self,playlist_id,song_id):
        return self.remove_songs(playlist_id,[song_id])

    def remove_songs(self,playlist_id,song_ids):
        def operation(db):
            playlist=self.playlist(db,playlist_id,True)
            existing={song.ID for song in playlist.Songs}
            if not set(song_ids)<=existing:raise ValueError("Playlist changed; refresh before removing entries")
            for id_ in dict.fromkeys(song_ids):db.remove_from_playlist(playlist,id_)
        return self.stage(f"Remove {len(song_ids)} playlist entries",operation,('remove_songs',[playlist_id,list(song_ids)]))

    def move_song(self,playlist_id,song_id,position):
        return self.stage(f"Move playlist entry to position {position}",lambda db:db.move_song_in_playlist(self.playlist(db,playlist_id,True),song_id,position),('move_song',[playlist_id,song_id,position]))

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
        values={key:value for key,value in values.items() if before[key]!=value}
        if not values:return
        detail="; ".join(f"{key}: {before[key]!s} → {value!s}" for key,value in values.items() if before[key]!=value)
        return self.stage(f"Metadata: {before['title']} — {detail}",operation,('edit_track',[id_,values]))

    def sync_local(self,playlist_id,target=None):
        local=next(p for p in self.catalogue.playlists() if p["id"]==playlist_id)
        if target is None:target=(self.state or {}).get("links",{}).get(playlist_id) or local["rekordbox_id"]
        entries=self.catalogue.entries(playlist_id)
        view=self.view()
        if target and not any(p["id"]==target for p in view["playlists"]):target=None
        native_paths={os.path.normcase(os.path.normpath(t["path"])) for t in view["tracks"]}
        files=self.catalogue.files();paths=[];skipped=[];converted=0;preferred_sources={}
        for entry in entries:
            original_id=entry.get('original_recording_id') or entry['recording_id']
            if entry['recording_id'] in preferred_sources and original_id!=preferred_sources[entry['recording_id']]:continue
            if entry['recording_id']:preferred_sources[entry['recording_id']]=original_id
            choices=[row for row in files if row["recording_id"]==entry["recording_id"] and row["available"]
                      and (path:=self.catalogue.resolve(row)) and path.is_file()] if entry["recording_id"] else []
            choices.sort(key=lambda row:(not bool(row.get('preferred')),os.path.normcase(os.path.normpath(str(self.catalogue.resolve(row)))) not in native_paths))
            row=choices[0] if choices else None
            path=self.catalogue.resolve(row) if row else None
            if not path or not path.is_file():skipped.append(entry);continue
            from .compatibility import compatible_path
            if os.path.normcase(os.path.normpath(str(path))) not in native_paths:
                original=path;path=compatible_path(self.catalogue,path);converted+=path!=original
            stat=path.stat();row=dict(row,size=stat.st_size,mtime_ns=stat.st_mtime_ns)
            paths.append((path,row))
        managed=[]
        saved_managed=(self.state or {}).get("managed_tracks",{}).get(playlist_id) or self.catalogue.get_setting("rekordbox_managed_tracks",{}).get(playlist_id,{})
        previous_managed=set(saved_managed.get("tracks",[])) if saved_managed.get("target")==target and saved_managed.get("source")==str(self.source) else set()
        def operation(db):
            from pyrekordbox.db6 import tables
            playlist=self.playlist(db,target,True) if target else db.create_playlist(local["name"])
            existing={}
            for content in db.get_content():existing.setdefault(os.path.normcase(os.path.normpath(content.FolderPath or "")),[]).append(content)
            desired=[]
            for path,row in paths:
                candidates=existing.get(os.path.normcase(os.path.normpath(str(path))),[])
                if len(candidates)>1:raise ValueError("Multiple Rekordbox track IDs share a file path; resolve this ambiguity before synchronising")
                content=candidates[0] if candidates else None
                if not content:
                    content=db.add_content(path,Title=row["title"],Length=int(row["duration"]),ISRC=row.get('isrc') or None,BPM=round(max(0,row.get('bpm',0))*100) or None,
                        ArtistID=name_id(db,tables.DjmdArtist,row["artist"]),AlbumID=name_id(db,tables.DjmdAlbum,row["album"]),GenreID=name_id(db,tables.DjmdGenre,row.get('genre') or ''))
                    existing[os.path.normcase(os.path.normpath(str(path)))]=[content]
                if not content.GenreID and row.get('genre'):content.GenreID=name_id(db,tables.DjmdGenre,row['genre'])
                desired.append(content)
            managed.extend(content.ID for content in desired)
            old={}
            for song in sorted(playlist.Songs,key=lambda s:s.TrackNo):old.setdefault(song.ContentID,[]).append(song)
            ordered=[]
            for content in desired:
                queue=old.get(content.ID,[])
                ordered.append(queue.pop(0) if queue else db.add_to_playlist(playlist,content))
            extras=[]
            for content_id,queue in old.items():
                for song in queue:
                    if content_id in previous_managed or content_id in managed:db.remove_from_playlist(playlist,song)
                    else:extras.append(song)
            ordered.extend(sorted(extras,key=lambda s:s.TrackNo))
            for index,song in enumerate(ordered,1):db.move_song_in_playlist(playlist,song,index)
            return playlist.ID
        result=self.stage(f"Sync local playlist {local['name']} ({len(paths)} available entries; {len(skipped)} missing skipped; {converted} MP3 compatibility copies)",operation,('sync_local',[playlist_id,target]))
        self.state.setdefault("local_snapshots",{})[playlist_id]={"name":local["name"],"entries":entries,
            "files":[{"path":str(path),"size":row["size"],"mtime_ns":row["mtime_ns"]} for path,row in paths]}
        self.state["links"][playlist_id]=result
        self.state.setdefault("managed_tracks",{})[playlist_id]={"source":str(self.source),"target":result,"tracks":managed}
        self.state.setdefault("skipped_tracks",{})[playlist_id]=[{"title":e["title"],"artist":e["artist"],"spotify_id":e["spotify_id"]} for e in skipped]
        self.save()
        return result

    def reorder_playlist(self,id_,song_ids):
        def operation(db):
            playlist=self.playlist(db,id_,True)
            songs={s.ID:s for s in playlist.Songs}
            if len(song_ids)!=len(set(song_ids)) or set(song_ids)!=set(songs):raise ValueError("Playlist changed; refresh before reordering")
            for position,song_id in enumerate(song_ids,1):db.move_song_in_playlist(playlist,songs[song_id],position)
        return self.stage("Reorder Rekordbox playlist",operation,('reorder_playlist',[id_,list(song_ids)]))

    def add_recordings(self,playlist_id,recording_ids):
        files=self.catalogue.files();paths=[]
        native_paths={os.path.normcase(os.path.normpath(t['path'])) for t in self.view()['tracks']}
        seen=set()
        for id_ in recording_ids:
            id_=self.catalogue.effective_recording(id_)
            if id_ in seen:continue
            seen.add(id_)
            row=next((r for r in files if r["recording_id"]==id_ and r["available"] and (p:=self.catalogue.resolve(r)) and p.is_file()),None)
            if not row:raise ValueError("Download or match every dragged track before adding to Rekordbox")
            from .compatibility import compatible_path
            path=self.catalogue.resolve(row)
            if os.path.normcase(os.path.normpath(str(path))) not in native_paths:path=compatible_path(self.catalogue,path)
            stat=path.stat()
            paths.append((path,dict(row,size=stat.st_size,mtime_ns=stat.st_mtime_ns)))
        def operation(db):
            from pyrekordbox.db6 import tables
            playlist=self.playlist(db,playlist_id,True)
            existing={}
            for content in db.get_content():existing.setdefault(os.path.normcase(os.path.normpath(content.FolderPath or "")),[]).append(content)
            for path,row in paths:
                key=os.path.normcase(os.path.normpath(str(path)));matches=existing.get(key,[])
                if len(matches)>1:raise ValueError("Multiple Rekordbox identities share this file; resolve that ambiguity first")
                content=matches[0] if matches else db.add_content(path,Title=row["title"],Length=int(row["duration"]),ISRC=row.get('isrc') or None,BPM=round(max(0,row.get('bpm',0))*100) or None,ArtistID=name_id(db,tables.DjmdArtist,row["artist"]),AlbumID=name_id(db,tables.DjmdAlbum,row["album"]))
                if not content.GenreID and row.get('genre'):content.GenreID=name_id(db,tables.DjmdGenre,row['genre'])
                existing[key]=[content];db.add_to_playlist(playlist,content)
        result=self.stage(f"Add {len(paths)} local track(s) to Rekordbox playlist",operation,('add_recordings',[playlist_id,list(recording_ids)]))
        self.state.setdefault("imported_files",[]).extend({"path":str(path),"size":row["size"],"mtime_ns":row["mtime_ns"]} for path,row in paths)
        self.save();return result

    def backups(self):
        return sorted((self.root/"backups").glob("*.zip"),key=lambda p:(p.stat().st_mtime_ns,p.name),reverse=True)

    def prune_backups(self,newest):
        limit=int(self.catalogue.get_setting('rekordbox_backup_limit',4))
        if limit<=0:return
        folder=(self.root/'backups').resolve();owned=[]
        for path in self.backups():
            if path.is_symlink() or path.resolve().parent!=folder:continue
            try:
                with zipfile.ZipFile(path) as archive:
                    if json.loads(archive.read('manifest.json')).get('format')=='zotibox-rekordbox-backup-1':owned.append(path)
            except (OSError,ValueError,KeyError,zipfile.BadZipFile):continue
        keep={newest}
        for path in owned:
            if len(keep)<limit:keep.add(path)
        errors=[]
        for path in owned:
            if path not in keep:
                try:path.unlink()
                except OSError:errors.append(path.name)
        self.retention_warning='Could not remove: '+', '.join(errors) if errors else ''

    def native_files(self,cancel=None):
        """Content hashes also detect changes to analysis/cues outside master.db."""
        result={}
        for path in self.source.parent.rglob("*"):
            if cancel and cancel.is_set():raise InterruptedError("Rekordbox comparison cancelled")
            if path.is_symlink() or path.is_junction():raise ValueError("Rekordbox data directory contains a linked folder")
            if path.is_file():result[path.relative_to(self.source.parent).as_posix()]=file_hash(path,cancel)
        return result

    def reusable_backup(self,files,cancel=None):
        """Only reuse a verified baseline after our own successful native write."""
        try:
            state=json.loads((self.root/"last-apply.json").read_text(encoding="utf-8"))
            archive=(self.root/"backups"/state["backup"]).resolve()
            if archive.parent!=(self.root/"backups").resolve():return None
            if state["source"]!=str(self.source) or state["files"]!=files:return None
            if not archive.is_file() or file_hash(archive,cancel)!=state["backup_sha256"]:return None
            return archive
        except (OSError,ValueError,TypeError,KeyError):
            return None

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
                try:self.prune_backups(archive)
                except Exception:self.retention_warning='The new backup is verified, but older backups could not be removed.'
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
            (self.root/"last-apply.json").unlink(missing_ok=True)
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
                raise ValueError("Rekordbox changed since the preview. Use Refresh & keep changes, then review and apply again. Your pending changes are kept.")
            self.verify(self.staged)
            progress(0,"Checking Rekordbox changes since the last ZotiBox apply")
            files=self.native_files(cancel)
            backup=self.reusable_backup(files,cancel)
            reused=backup is not None
            if backup is None:backup=self.backup(progress,cancel,"before-write")
            if cancel and cancel.is_set():raise InterruptedError("Apply cancelled; live library unchanged")
            closed()
            if fingerprint(self.source)!=self.state["fingerprint"]:raise ValueError("Rekordbox changed during backup; nothing applied")
            replacements=[(self.staged,self.source)]
            xml=self.staged.parent/"masterPlaylists6.xml"
            if xml.exists():replacements.append((xml,self.source.parent/xml.name))
            for relative,digest in self.state.get('analysis_originals',{}).items():
                original=(self.source.parent/relative).resolve()
                overlay=(self.staged.parent/'native-files'/relative).resolve()
                if not original.is_relative_to(self.source.parent) or not overlay.is_relative_to(self.staged.parent/'native-files'):
                    raise ValueError('Invalid analysis path in preview')
                if file_hash(original)!=digest:raise ValueError('Rekordbox analysis changed since this preview; refresh before applying')
                replacements.append((overlay,original))
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
            for _,target in replacements:
                files[target.relative_to(self.source.parent).as_posix()]=file_hash(target)
            atomic_json(self.root/"last-apply.json",{"source":str(self.source),"files":files,
                "backup":backup.name,"backup_sha256":file_hash(backup)})
            with self.catalogue.connect() as db:
                for id_ in self.state.get("deleted_playlists",[]):db.execute("UPDATE playlists SET rekordbox_id=NULL WHERE rekordbox_id=?",(id_,))
                for local,target in self.state["links"].items():db.execute("UPDATE playlists SET rekordbox_id=? WHERE id=?",(target,local))
            managed=self.catalogue.get_setting("rekordbox_managed_tracks",{})
            managed.update(self.state.get("managed_tracks",{}))
            for item in managed.values():
                item['tracks']=[self.state.get('merged_tracks',{}).get(i,i) for i in item.get('tracks',[])]
            self.catalogue.set_setting("rekordbox_managed_tracks",managed)
            self.state["changes"]=[];self.connect()
            return f"Applied Rekordbox changes; {'retained existing backup' if reused else 'new backup'}: {backup.name}"

    @staticmethod
    def verify(path):
        with database(path) as db:
            from sqlalchemy import text
            if db.session.execute(text("PRAGMA integrity_check")).scalar()!="ok":raise ValueError("Rekordbox database integrity check failed")
            if db.session.execute(text("PRAGMA foreign_key_check")).first():raise ValueError("Rekordbox database contains broken references")
