"""Serializable edit recipes and conservative conflict checks for preview refreshes."""
from copy import deepcopy


class PreviewConflict(ValueError):
    pass


def capture(view, method, args):
    playlists={p['id']:p for p in view['playlists']}
    tracks={t['id']:t for t in view['tracks']}
    guard={}
    if method=='edit_track':
        guard['track']={key:tracks[args[0]][key] for key in args[1]}
    elif method not in {'create_playlist'}:
        target=args[1] if method=='sync_local' else args[0]
        if target in playlists:
            fields=['id','name','parent','type']
            if method in {'delete_playlist','remove_songs','move_song','reorder_playlist'}:fields.append('songs')
            guard['playlist']=deepcopy({key:playlists[target][key] for key in fields})
        if method=='delete_playlist' and target in playlists:
            descendants={target}
            while True:
                children={p['id'] for p in playlists.values() if p['parent'] in descendants}
                if children<=descendants:break
                descendants|=children
            guard['descendants']=[deepcopy(playlists[i]) for i in sorted(descendants)]
    return {'method':method,'args':deepcopy(args),'guard':guard}


def created(before, after):
    """Only newly allocated identities need translation when edits are replayed."""
    tracks={t['id'] for t in before['tracks']}
    playlists={p['id'] for p in before['playlists']}
    songs={s['id'] for p in before['playlists'] for s in p['songs']}
    return {'tracks':[{k:t[k] for k in ('id','path')} for t in after['tracks'] if t['id'] not in tracks],
        'playlists':[p['id'] for p in after['playlists'] if p['id'] not in playlists],
        'songs':[dict(s,playlist=p['id']) for p in after['playlists'] for s in p['songs'] if s['id'] not in songs]}


def prepare(record, view, identities):
    method=record['method'];args=deepcopy(record['args']);guard=record['guard']
    if method not in {'create_playlist','rename_playlist','move_playlist','delete_playlist','add_tracks','remove_songs','move_song','edit_track','sync_local','reorder_playlist','add_recordings'}:
        raise PreviewConflict('This edit cannot be refreshed automatically. Your pending changes are kept.')
    remap=lambda value:identities['playlists'].get(value,value)
    track_id=lambda value:identities['tracks'].get(value,value)
    song_id=lambda value:identities['songs'].get(value,value)
    if method=='create_playlist':args[1]=remap(args[1])
    elif method=='sync_local':args[1]=remap(args[1])
    else:args[0]=track_id(args[0]) if method=='edit_track' else remap(args[0])
    if method=='move_playlist':args[1]=remap(args[1])
    if method in {'add_tracks','remove_songs','reorder_playlist'}:args[1]=[(track_id(i) if method=='add_tracks' else song_id(i)) for i in args[1]]
    if method=='move_song':args[1]=song_id(args[1])
    playlists={p['id']:p for p in view['playlists']}
    tracks={t['id']:t for t in view['tracks']}
    def conflict(detail):
        raise PreviewConflict(f"{detail}\nYour pending changes are kept. Undo the conflicting preview edit, or resolve the item in Rekordbox, then use Refresh & keep changes again.")
    if 'track' in guard:
        track=tracks.get(args[0])
        if track is None:conflict('A track you edited was removed from Rekordbox.')
        for key,value in guard['track'].items():
            if track[key]!=value and track[key]!=args[1][key]:conflict(f"{track['title']}: {key} changed in both Rekordbox and your preview.")
    if 'playlist' in guard:
        old=guard['playlist'];target=remap(old['id']);current=playlists.get(target)
        if current is None or current['type']!=old['type']:conflict(f"{old['name']}: the playlist was removed or its type changed in Rekordbox.")
        if method=='rename_playlist' and current['name'] not in {old['name'],args[1].strip()}:conflict(f"{old['name']}: the name changed in both places.")
        if method=='move_playlist' and current['parent'] not in {remap(old['parent']),args[1] or 'root'}:conflict(f"{old['name']}: the folder changed in both places.")
        expected=[(song_id(s['id']),track_id(s['track_id'])) for s in old.get('songs',[])]
        actual=[(s['id'],s['track_id']) for s in current['songs']]
        if method in {'reorder_playlist','move_song'} and actual!=expected:conflict(f"{old['name']}: its tracks or order changed in Rekordbox while you were reordering it.")
        if method=='remove_songs' and not set(args[1])<={s['id'] for s in current['songs']}:conflict(f"{old['name']}: an entry you removed has changed in Rekordbox.")
    for old in guard.get('descendants',[]):
        current=playlists.get(remap(old['id']))
        expected=deepcopy(old);expected['id']=remap(old['id']);expected['parent']=remap(old['parent'])
        expected['songs']=[dict(s,id=song_id(s['id']),track_id=track_id(s['track_id'])) for s in old['songs']]
        if current is None or any(current[k]!=expected[k] for k in ('name','parent','type','songs')):conflict(f"{old['name']}: a playlist marked for deletion changed in Rekordbox.")
    if guard.get('descendants'):
        deleted={remap(p['id']) for p in guard['descendants']}
        if any(p['parent'] in deleted and p['id'] not in deleted for p in playlists.values()):conflict('A folder marked for deletion now contains a new playlist.')
    return method,args


def translate(old, new, identities):
    if len(old['playlists'])!=len(new['playlists']):raise PreviewConflict('The refreshed playlist identities are ambiguous. Your pending changes are kept.')
    identities['playlists'].update(zip(old['playlists'],new['playlists']))
    for track in old['tracks']:
        candidates=[t['id'] for t in new['tracks'] if t['path']==track['path']]
        if len(candidates)!=1:raise PreviewConflict('An imported track changed identity. Your pending changes are kept.')
        identities['tracks'][track['id']]=candidates[0]
    remaining=list(new['songs'])
    for song in old['songs']:
        match=next((s for s in remaining if s['playlist']==identities['playlists'].get(song['playlist'],song['playlist']) and s['track_id']==identities['tracks'].get(song['track_id'],song['track_id'])),None)
        if match is None:raise PreviewConflict('A playlist entry could not be carried forward. Your pending changes are kept.')
        identities['songs'][song['id']]=match['id'];remaining.remove(match)
