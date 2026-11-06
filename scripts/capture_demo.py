"""Capture public UI screenshots with generated music and fictional metadata only."""
import sys,uuid,wave,time,argparse
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'desktop'))
from PySide6.QtCore import Qt,QBuffer,QIODevice
from PySide6.QtGui import QImage,QColor,QPainter,QLinearGradient
from PySide6.QtWidgets import QApplication
from mutagen.wave import WAVE
from mutagen.id3 import TIT2,TPE1,TALB,APIC,TBPM
from dj_library.ui import Window

root=Path(__file__).resolve().parents[1]
parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument('--output',type=Path,default=root/'docs'/'images')
parser.add_argument('--width',type=int,default=1500)
parser.add_argument('--height',type=int,default=920)
parser.add_argument('--theme',default='Charcoal / Mint')
args=parser.parse_args()
workspace=root/'.tmp'/('public-demo-'+uuid.uuid4().hex)
music=workspace/'Music';music.mkdir(parents=True)
app=QApplication([]);window=Window(workspace/'Database');window.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen)
c=window.catalogue
names=[('Northbound','Low Signal','Warm-up'),('Glass Harbour','Lumen','Warm-up'),('After Hours','Late District','Warm-up'),
    ('Blue Hour','Low Signal','Warm-up'),('Transit','Sidechain','Peak time'),('Open Circuit','Lumen','Peak time'),
    ('Nightfall','Late District','Peak time'),('Parallel Lines','Sidechain','Peak time'),('Fading Light','Lumen','Closing'),
    ('Slow Return','Low Signal','Closing'),('Harbour Lights','Late District','Closing'),('Daybreak','Sidechain','Closing')]
for i,(title,artist,folder) in enumerate(names):
    path=music/folder/(artist+' - '+title+'.wav');path.parent.mkdir(exist_ok=True)
    with wave.open(str(path),'wb') as audio:audio.setparams((1,2,8000,8000,'NONE','not compressed'));audio.writeframes(b'\0\0'*8000)
    tags=WAVE(path);tags.add_tags()
    for kind,text in [(TIT2,title),(TPE1,artist),(TALB,'Demo collection'),(TBPM,str(120+i))]:tags.tags.add(kind(encoding=3,text=[text]))
    image=QImage(160,160,QImage.Format.Format_RGB32);p=QPainter(image);gradient=QLinearGradient(0,0,160,160)
    gradient.setColorAt(0,QColor.fromHsv((i*37)%360,150,130));gradient.setColorAt(1,QColor.fromHsv((i*37+60)%360,210,45));p.fillRect(image.rect(),gradient)
    p.setPen(QColor('#e9edef'));font=p.font();font.setPixelSize(54);p.setFont(font);p.drawText(image.rect(),Qt.AlignmentFlag.AlignCenter,f'{i+1:02d}');p.end()
    data=QBuffer();data.open(QIODevice.OpenModeFlag.WriteOnly);image.save(data,'PNG');tags.tags.add(APIC(encoding=3,mime='image/png',type=3,data=bytes(data.data())));tags.save()
c.add_root(music);c.scan();files=c.files()
with c.connect() as db:
    for i,row in enumerate(files):db.execute('UPDATE recordings SET duration=? WHERE id=?',(193+i*17,row['recording_id']))
folder=c.create_playlist_folder('DJ sets');warm=c.create_playlist_folder('Warm-up',folder);peak=c.create_playlist_folder('Peak time',folder)
tracks=[];resolved={}
for i,row in enumerate(c.files()[:8]):
    t={'id':f'{i:022d}','title':row['title'],'artist':row['artist'],'album':'Demo collection','duration':row['duration'],'playable':True}
    tracks.append(t);resolved[t['id']]={'recording_id':row['recording_id'],'state':'matched'}
for i in range(4):
    t={'id':f'{i+100:022d}','title':['Last Train','Orbit','Long Way Home','Signal Path'][i],'artist':['Lumen','Low Signal','Late District','Sidechain'][i],
        'album':'Demo collection','duration':240+i*20,'playable':True};tracks.append(t);resolved[t['id']]={'recording_id':None,'state':'missing'}
p=c.sync_playlist({'id':'9'*22,'name':'Friday warm-up','snapshot':'demo','complete':True,'tracks':tracks},resolved)['playlist_id']
c.move_playlist_item(p,warm)
for name,parent in [('Opening set',warm),('Main room',peak),('Closing set',folder)]:
    local=c.create_playlist(name);c.move_playlist_item(local,parent);c.add_entries(local,[r['recording_id'] for r in c.files()[:6]])
    if name=='Opening set':compare_id=local;c.add_entries(local,[r['recording_id'] for r in c.files()[-2:]])
c.set_setting('playlist_expanded',[folder,warm,peak]);window.playlist_id=p;window.refresh();window.resize(args.width,args.height);window.choose_theme_preset(args.theme);window.show()
window.workspace_label.setText('Demo music library');output=args.output;output.mkdir(parents=True,exist_ok=True)
def capture(name,page):
    window.navigation.setCurrentRow(page)
    for _ in range(40):app.processEvents();time.sleep(.015)
    window.grab().save(str(output/name))
window.files_table.expandAll();capture('local-music.png',2);capture('playlists.png',3)
from PySide6.QtCore import QItemSelectionModel
native=window.rb_panel;previous_state=native.backend.state;previous_data=native.data
native.backend.state={'changes':['Rename playlist: Friday warm-up','Sync Friday warm-up · 8 matched tracks'],'history':[]}
native.preview_loaded=True
demo_tracks=[dict(row,id=str(i),path=str(c.resolve(row)),genre='House',rating=0,color='',bpm=120+i) for i,row in enumerate(c.files()[:8])]
native.loaded({'tracks':demo_tracks,'playlists':[{'id':'demo-set','name':'Friday warm-up','parent':'root','type':0,'seq':1,
    'songs':[{'id':f'song-{i}','track_id':t['id'],'position':i+1} for i,t in enumerate(demo_tracks)]}],
    'colors':[],'changes':native.backend.state['changes']})
window.playlist_tabs.setCurrentIndex(1);native.tree.setCurrentItem(native.tree.topLevelItem(1));capture('rekordbox-preview.png',3)
native.backend.state=previous_state;native.data=previous_data;native.refresh_tree();window.playlist_tabs.setCurrentIndex(0)
for i,t in enumerate(tracks[-4:]):c.enqueue(t['id'],{'key':'spotify:playlist:'+('9' if i%2==0 else '8')*22,'name':'Friday warm-up' if i%2==0 else 'Night drive','position':i})
job=c.download_jobs()[0];c.update_download(job['id'],state='running',progress=42,message='Downloading · real-time speed')
window.download_job=object();window.refresh_downloads();window.download_tabs.setCurrentIndex(1);window.downloads_table.selectRow(1);capture('downloads.png',1)
window.download_job=None;window.zotify_settings();window.zotify_panel.loading=True;window.zotify_panel.destination.setText(r'C:\Music\ZotiBox');window.zotify_panel.loading=False;capture('zotify-settings.png',4)
from dj_library.playlist_compare import ComparisonDialog,playlist_sources
dialog=ComparisonDialog(playlist_sources(c,{}),window,initial=['local:'+p,'local:'+compare_id]);dialog.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen);dialog.show()
for _ in range(20):app.processEvents();time.sleep(.01)
dialog.grab().save(str(output/'comparison.png'));dialog.close()
from dj_library.organisation_ui import OrganiseDialog
dialog=OrganiseDialog(window);dialog.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen)
plan=dialog.service.plan('consolidate',workspace/'Output')
dialog.display_plan(plan);dialog.destination.setText(r'C:\Music\ZotiBox')
for i,row in enumerate(plan['rows']):
    dialog.preview.item(i,0).setText('D:\\Music\\'+Path(row['source']).name)
    dialog.preview.item(i,1).setText('C:\\Music\\ZotiBox\\'+Path(row['target']).name)
dialog.show()
for _ in range(20):app.processEvents();time.sleep(.01)
dialog.grab().save(str(output/'organisation.png'));dialog.close()
window.rb_panel.preview_loaded=True
window.navigation.setCurrentRow(3);window.playlist_tabs.setCurrentIndex(2);window.all_tracks.unassigned.setChecked(True)
capture('all-tracks.png',3)
window.download_job=None;window.close()
print(f'Captured demo screenshots in {output} at {window.width()} x {window.height()}')
