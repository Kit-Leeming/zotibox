"""Capture public UI screenshots with generated music and fictional metadata only."""
import sys,uuid,wave,time
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'desktop'))
from PySide6.QtCore import Qt,QBuffer,QIODevice
from PySide6.QtGui import QImage,QColor,QPainter,QLinearGradient
from PySide6.QtWidgets import QApplication
from mutagen.wave import WAVE
from mutagen.id3 import TIT2,TPE1,TALB,APIC
from dj_library.ui import Window

root=Path(__file__).resolve().parents[1]
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
    for kind,text in [(TIT2,title),(TPE1,artist),(TALB,'Demo collection')]:tags.tags.add(kind(encoding=3,text=[text]))
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
c.set_setting('playlist_expanded',[folder,warm,peak]);window.playlist_id=p;window.refresh();window.resize(1500,920);window.show()
window.workspace_label.setText('Demo music library');output=root/'docs'/'images';output.mkdir(parents=True,exist_ok=True)
def capture(name,page):
    window.navigation.setCurrentRow(page)
    for _ in range(40):app.processEvents();time.sleep(.015)
    image=window.grab().toImage();image=image.scaledToWidth(1500,Qt.TransformationMode.SmoothTransformation);image.save(str(output/name))
window.files_table.expandAll();capture('local-music.png',2);capture('playlists.png',3)
for t in tracks[-4:]:c.enqueue(t['id'])
job=c.download_jobs()[0];c.update_download(job['id'],state='running',progress=42,message='Downloading · real-time speed')
window.download_job=object();window.refresh_downloads();window.download_tabs.setCurrentIndex(1);capture('downloads.png',1)
window.download_job=None;window.close()
print('Captured demo screenshots in docs/images')
