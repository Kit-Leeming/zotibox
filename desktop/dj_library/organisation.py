"""Reviewed, journaled copies. Originals are removed only by explicit cleanup."""
import os
import shutil
import subprocess
import threading
import uuid
from pathlib import Path
from .catalogue import atomic_json,file_hash,now,uid,read_metadata
from .downloads import safe_name


def path_key(path):return os.path.normcase(os.path.normpath(str(Path(path).resolve())))


class Organiser:
    def __init__(self,catalogue,rekordbox):
        self.catalogue=catalogue;self.rekordbox=rekordbox
        self.journals=catalogue.workspace/'.local'/'organisation';self.journals.mkdir(parents=True,exist_ok=True)

    def plan(self,kind,destination=None,files=None,keeper=None):
        c=self.catalogue;files=files if files is not None else [f for f in c.files() if f['available']]
        destination=Path(destination or c.get_setting('download_folder',str(c.workspace/'Music'))).resolve()
        music=Path(c.get_setting('download_folder',str(c.workspace/'Music'))).resolve()
        if kind=='move' and c.workspace.is_relative_to(music):raise ValueError('The download folder contains the database. Use a dedicated music folder before moving it.')
        if kind in {'move','consolidate'} and c.workspace.is_relative_to(destination):raise ValueError('Choose a music folder that does not contain the database')
        if kind=='move' and (destination==music or destination.is_relative_to(music) or music.is_relative_to(destination)):
            raise ValueError('Choose a separate destination outside the existing download folder')
        rows=[];used={};by_hash={}
        preferred_recording=None
        if kind=='version':
            preferred=next((r for r in c.files() if r['available'] and (p:=c.resolve(r)) and path_key(p)==path_key(keeper)),None)
            if not preferred:raise ValueError('Scan the preferred file before using it as a replacement')
            preferred_recording=preferred['recording_id']
        for f in files:
            source=c.resolve(f)
            if not source or not source.is_file():continue
            if kind=='move' and not source.is_relative_to(music):continue
            if kind in {'duplicates','version'}:target=Path(keeper).resolve(strict=True)
            elif kind=='move':target=destination/source.relative_to(music)
            elif kind=='convert':target=None
            else:
                if f['sha256'] in by_hash:target=by_hash[f['sha256']]
                else:
                    target=destination/(safe_name(f"{f['artist'] or 'Unknown artist'} - {f['title']}")[:170]+source.suffix.lower())
                    if (path_key(target) in used and used[path_key(target)]!=f['sha256']) or (target.exists() and file_hash(target)!=f['sha256']):
                        target=target.with_stem(target.stem+' - '+f['sha256'][:12])
                    by_hash[f['sha256']]=target
            if target and path_key(source)==path_key(target):continue
            if kind=='version' and f['recording_id']!=preferred_recording and c.effective_recording(preferred_recording)==f['recording_id']:
                raise ValueError('This would reverse an existing preferred-version link. Clear that preference first.')
            if target:
                if kind in {'move','consolidate'} and not target.resolve().is_relative_to(destination):raise ValueError('Destination contains a link outside the selected folder')
                if kind!='version' and target.exists() and file_hash(target)!=f['sha256']:raise ValueError('Destination contains different audio: '+str(target))
                used[path_key(target)]=f['sha256']
            rows.append({'file_id':f['id'],'recording_id':f['recording_id'],'source':str(source),'target':str(target) if target else '',
                    'sha256':f['sha256'],'size':f['size'],'title':f['title'],
                    'preferred_sha256':file_hash(target) if kind=='version' else None})
        if kind=='move' and music.is_dir():
            known={path_key(r['source']) for r in rows}
            # Include unindexed music, covers, lyrics and conversion receipts.
            for directory,subdirs,names in os.walk(music,followlinks=False):
                if any((Path(directory)/n).is_symlink() or (Path(directory)/n).is_junction() for n in subdirs):
                    raise ValueError('Linked directories must be moved separately')
                for name in names:
                    source=Path(directory)/name
                    if source.is_symlink():raise ValueError('Linked files must be moved separately')
                    if path_key(source) in known:continue
                    target=destination/source.relative_to(music);digest=file_hash(source)
                    if target.exists() and file_hash(target)!=digest:raise ValueError('Destination contains a different file: '+str(target))
                    rows.append({'file_id':None,'recording_id':None,'source':str(source),'target':str(target),'sha256':digest,'size':source.stat().st_size,'title':name})
        if not rows:raise ValueError('No files need this operation')
        # Adding a parent as a new scan root would overlap existing bindings.
        if kind in {'move','consolidate'}:
            if any(r['path'] and Path(r['path']).resolve()!=destination and Path(r['path']).resolve().is_relative_to(destination) for r in c.roots()):
                raise ValueError('Destination contains an existing scan root. Choose that root itself or a separate folder.')
        existing_parent=destination
        while not existing_parent.exists():existing_parent=existing_parent.parent
        required=sum(r['size'] for r in rows if not r['target'] or not Path(r['target']).exists())
        if kind=='move':required+=sum(r['size'] for r in rows)
        if shutil.disk_usage(existing_parent).free<required+16*1024*1024:raise ValueError('Not enough free space for verified copies')
        return {'id':uid(),'kind':kind,'destination':str(destination),'source_folder':str(music),'created':now(),'state':'planned','rows':rows,'preferred_recording':preferred_recording}

    def save(self,plan):atomic_json(self.journals/(plan['id']+'.json'),plan)

    def execute(self,plan,progress=lambda *a:None,cancel=None):
        c=self.catalogue;cancel=cancel or threading.Event()
        self.save(plan)
        if self.rekordbox.state and self.rekordbox.state['changes']:raise ValueError('Apply or discard existing Rekordbox changes before organising files')
        if self.rekordbox.source.is_file():self.rekordbox.connect()
        plan['native_required']=bool(self.rekordbox.state);plan['state']='copying';self.save(plan)
        with c.scan_lock:
            staged=None
            try:
                for row in plan['rows']:
                    if cancel.is_set():raise InterruptedError('Organisation paused; originals retained')
                    if row['file_id']:
                        with c.connect() as db:cached=db.execute('SELECT * FROM files WHERE id=?',(row['file_id'],)).fetchone()
                        if not cached or cached['sha256']!=row['sha256'] or path_key(c.resolve(cached))!=path_key(row['source']):
                            raise ValueError('The catalogue changed. Prepare a new organisation preview.')
                    if file_hash(Path(row['source']),cancel)!=row['sha256']:raise ValueError('A source changed. Scan again and prepare a new plan: '+row['source'])
                if plan['kind']=='move' and os.name=='nt':
                    destination=Path(plan['destination']);destination.mkdir(parents=True,exist_ok=True)
                    staged=destination/('.zotibox-copy-'+plan['id']);staged.mkdir(exist_ok=True)
                    args=['robocopy',plan['source_folder'],str(staged),'/E','/XJ','/COPY:DAT','/DCOPY:DT','/R:1','/W:1','/NFL','/NDL','/NJH','/NJS','/NP']
                    log=self.journals/(plan['id']+'-robocopy.log')
                    with log.open('wb') as output:
                        process=subprocess.Popen(args,stdout=output,stderr=subprocess.STDOUT,creationflags=subprocess.CREATE_NO_WINDOW)
                        while process.poll() is None:
                            if cancel.wait(.1):process.terminate();process.wait();raise InterruptedError('Copy cancelled; originals retained')
                        if process.returncode>=8:raise ValueError('Robocopy failed; see '+str(log))
                for index,row in enumerate(plan['rows']):
                    if cancel.is_set():raise InterruptedError('Organisation paused; originals retained')
                    source=Path(row['source'])
                    if plan['kind']=='convert':
                        from .compatibility import compatible_path
                        target=compatible_path(c,source);row['target']=str(target)
                    elif plan['kind']=='version':
                        target=Path(row['target'])
                        if file_hash(target,cancel)!=row['preferred_sha256']:raise ValueError('Preferred version changed since the preview')
                    else:
                        target=Path(row['target']);target.parent.mkdir(parents=True,exist_ok=True)
                        if not target.exists():
                            temporary=target.parent/('.zotibox-'+uuid.uuid4().hex+'.part')
                            try:
                                origin=staged/source.relative_to(plan['source_folder']) if staged else source
                                shutil.copy2(origin,temporary)
                                if file_hash(temporary,cancel)!=row['sha256']:raise ValueError('Copied file failed verification; originals retained')
                                if target.exists():raise ValueError('Destination changed during copy; nothing overwritten')
                                temporary.rename(target)
                            finally:temporary.unlink(missing_ok=True)
                        if file_hash(target,cancel)!=row['sha256']:raise ValueError('Destination failed verification')
                    row['target_sha256']=file_hash(target,cancel);row['copied']=True;self.save(plan)
                    if staged:
                        staged_file=(staged/source.relative_to(plan['source_folder'])).resolve()
                        if staged_file.is_relative_to(staged.resolve()) and staged_file.is_file() and file_hash(staged_file)==row['sha256']:
                            staged_file.unlink()
                    progress(index+1,target.name)
                mapping={r['source']:r['target'] for r in plan['rows']}
                if self.rekordbox.state:self.rekordbox.relocate_files(mapping,merge=plan['kind'] in {'duplicates','consolidate','version'},replace_audio=plan['kind']=='version')
                plan['state']='relinked-preview';self.save(plan)
                if plan['kind']=='version':
                    for row in plan['rows']:
                        if row['recording_id'] and row['recording_id']!=plan['preferred_recording']:
                            c.prefer_version(row['recording_id'],plan['preferred_recording'])
                destination=Path(plan['destination'])
                if plan['kind'] in {'move','consolidate'} and not any(r['path'] and destination.is_relative_to(Path(r['path']).resolve()) for r in c.roots()):
                    c.add_root(destination)
                # Preserve file/recording identities. New locations are indexed
                # explicitly instead of asking a scan to guess substitutions.
                for row in plan['rows']:
                    if not row['file_id']:continue
                    target=Path(row['target']);stat=target.stat()
                    root=next((r for r in c.roots() if r['path'] and target.is_relative_to(Path(r['path']).resolve())),None)
                    root_id=root['id'] if root else c.add_root(target.parent)
                    binding=Path(c.bindings()[root_id]);relative=target.relative_to(binding).as_posix()
                    with c.connect() as db:
                        prior=db.execute('SELECT * FROM files WHERE id=?',(row['file_id'],)).fetchone()
                        existing=db.execute('SELECT * FROM files WHERE root_id=? AND relative_path=?',(root_id,relative)).fetchone()
                        if existing and existing['recording_id']!=prior['recording_id']:
                            # Exact duplicate identities share a canonical row.
                            recording=existing['recording_id']
                            if plan['kind']!='version':
                                db.execute('UPDATE entries SET recording_id=? WHERE recording_id=?',(recording,prior['recording_id']))
                                db.execute('UPDATE matches SET recording_id=? WHERE recording_id=?',(recording,prior['recording_id']))
                        else:recording=prior['recording_id']
                        target_id=existing['id'] if existing else uid()
                        if not existing:
                            columns=list(prior.keys());values=dict(prior);values.update(id=target_id,root_id=root_id,relative_path=relative,sha256=row['target_sha256'],size=stat.st_size,mtime_ns=stat.st_mtime_ns,available=1,codec=target.suffix[1:].upper())
                            if plan['kind']=='convert':
                                metadata=read_metadata(target)
                                for key in ('codec','bitrate','sample_rate','bits','bpm'):values[key]=metadata[key]
                            db.execute('INSERT INTO files ('+','.join(columns)+') VALUES ('+','.join('?' for _ in columns)+')',[values[k] for k in columns])
                        db.execute('UPDATE recordings SET preferred_file_id=? WHERE id=?',(target_id,recording))
                        db.execute('UPDATE downloads SET output=? WHERE output=?',(str(target),row['source']))
                        c.change(db,'file',target_id,'organise')
                    if existing and existing['recording_id']!=prior['recording_id'] and plan['kind']!='version':
                        c.prefer_version(prior['recording_id'],existing['recording_id'])
                    row['target_id']=target_id;self.save(plan)
                if plan['kind']=='move':c.set_setting('download_folder',plan['destination'])
                sources=c.get_setting('compatibility_sources',{})
                for row in plan['rows']:
                    old=sources.get(os.path.normcase(row['source']))
                    if old:sources[os.path.normcase(row['target'])]=old
                c.set_setting('compatibility_sources',sources)
                c.file_revision+=1;plan['state']='ready-for-cleanup';self.save(plan)
                return plan
            except BaseException as exc:
                plan['error']=str(exc);self.save(plan);raise
            finally:
                # Remove only empty directories within our own verified stage.
                # Unexpected/new files remain recoverable there.
                if staged and staged.resolve().parent==Path(plan['destination']).resolve():
                    for directory,_,_ in os.walk(staged,topdown=False):
                        value=Path(directory).resolve()
                        if value.is_relative_to(staged.resolve()):
                            try:value.rmdir()
                            except OSError:pass

    def cleanup(self,plan,remove,progress=lambda *a:None,cancel=None):
        from .rekordbox import closed
        closed()
        if plan['state']!='ready-for-cleanup':raise ValueError('Finish copying and relinking first')
        if self.rekordbox.state and self.rekordbox.state['changes']:raise ValueError('Apply the Rekordbox preview before removing originals')
        if self.rekordbox.source.is_file():
            self.rekordbox.connect()
            native_paths={path_key(t['path']) for t in self.rekordbox.view()['tracks'] if t['path']}
        elif plan.get('native_required'):raise ValueError('The Rekordbox database is unavailable; originals retained')
        else:native_paths=set()
        for row in plan['rows']:
            if cancel and cancel.is_set():raise InterruptedError('Cleanup paused')
            source,target=Path(row['source']),Path(row['target'])
            if row.get('removed'):continue
            if path_key(source)==path_key(target):continue
            if path_key(source) in native_paths:raise ValueError('Rekordbox still references '+str(source))
            if file_hash(target)!=row['target_sha256'] or (source.exists() and file_hash(source)!=row['sha256']):raise ValueError('A file changed; original retained')
            if source.exists():
                closed();remove(source)
            if source.exists():raise ValueError('Removal did not complete; original retained')
            if row['file_id']:
                with self.catalogue.connect() as db:
                    db.execute('INSERT OR IGNORE INTO retired_files SELECT sha256,recording_id FROM files WHERE id=?',(row['file_id'],))
                    db.execute('DELETE FROM files WHERE id=?',(row['file_id'],))
                    db.execute('UPDATE downloads SET output=? WHERE output=?',(str(target),str(source)))
                self.catalogue.file_revision+=1
            row['removed']=True;self.save(plan);progress(1,source.name)
        plan['state']='complete';self.save(plan);return plan
