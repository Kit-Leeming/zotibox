"""Visible-row cover reads stay off the UI thread; unavailable storage cannot block exit."""
from collections import OrderedDict
import queue,threading
from pathlib import Path
from PySide6.QtCore import QObject,Signal
from PySide6.QtGui import QIcon
from .artwork import cover

class ArtworkCache(QObject):
    ready=Signal(str,object)
    def __init__(self,parent):
        super().__init__(parent);self.images=OrderedDict();self.pending=set();self.queue=queue.Queue(128);self.stop=threading.Event();self.started=False
    def get(self,path):return self.images.get(str(path),QIcon())
    def request(self,path):
        path=str(path)
        if self.stop.is_set() or path in self.images or path in self.pending:return
        if not self.started:
            self.started=True
            for _ in range(2):threading.Thread(target=self.worker,daemon=True).start()
        try:self.queue.put_nowait(path);self.pending.add(path)
        except queue.Full:pass
    def worker(self):
        while not self.stop.is_set():
            try:path=self.queue.get(timeout=.1)
            except queue.Empty:continue
            try:
                stat=Path(path).stat();image=cover(path,stat.st_size,stat.st_mtime_ns,True)
            except Exception:image=None
            if not self.stop.is_set():
                try:self.ready.emit(path,image)
                except RuntimeError:return
    def save(self,path,icon):
        self.pending.discard(path);self.images[path]=icon;self.images.move_to_end(path)
        while len(self.images)>1024:self.images.popitem(last=False)
    def close(self):self.stop.set()
