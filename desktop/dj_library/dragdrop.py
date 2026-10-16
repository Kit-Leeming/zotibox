"""Internal song drags, drop targets and delayed navigation while dragging."""
import json
from PySide6.QtCore import Qt,QMimeData,QTimer,QObject,QEvent
from PySide6.QtGui import QDrag
from PySide6.QtWidgets import QTableWidget,QListWidget
MIME="application/x-zotibox-tracks"

def decode(mime):
    if not mime.hasFormat(MIME):return None
    try:
        if mime.data(MIME).size()>1024*1024:return None
        result=json.loads(bytes(mime.data(MIME)))
        return result if isinstance(result,dict) else None
    except (ValueError,TypeError):return None

class DragMixin:
    def startDrag(self,actions):
        callback=getattr(self,'drag_payload',None);payload=callback() if callback else None
        if not payload:return
        mime=QMimeData();mime.setData(MIME,json.dumps(payload).encode())
        drag=QDrag(self);drag.setMimeData(mime)
        action=Qt.DropAction.MoveAction if payload.get('kind') in {'playlist','queue'} else Qt.DropAction.CopyAction
        self._dragging=True
        try:drag.exec(Qt.DropAction.CopyAction|Qt.DropAction.MoveAction,action)
        finally:self._dragging=False
    def dragEnterEvent(self,event):
        if decode(event.mimeData()) and getattr(self,'drop_tracks',None):event.acceptProposedAction()
        else:event.ignore()
    def dragMoveEvent(self,event):
        self.dragEnterEvent(event)
        if event.isAccepted() and hasattr(self,'topLevelItemCount'):
            item=self.itemAt(event.position().toPoint())
            if item is not getattr(self,'_hover_item',None):
                self._hover_item=item
                if not hasattr(self,'_expand_timer'):
                    self._expand_timer=QTimer(self);self._expand_timer.setSingleShot(True);self._expand_timer.setInterval(650)
                    self._expand_timer.timeout.connect(self._expand_hover)
                self._expand_timer.start()
    def _expand_hover(self):
        item=getattr(self,'_hover_item',None)
        try:
            if item and item.treeWidget() is self:item.setExpanded(True)
        except RuntimeError:self._stop_hover() # A background refresh may have replaced the tree items.
    def _stop_hover(self):
        if hasattr(self,'_expand_timer'):self._expand_timer.stop()
        self._hover_item=None
    def dragLeaveEvent(self,event):self._stop_hover();event.accept()
    def dropEvent(self,event):
        self._stop_hover()
        data=decode(event.mimeData());callback=getattr(self,'drop_tracks',None)
        if data and callback and callback(data,event.position().toPoint()):event.acceptProposedAction()
        else:event.ignore()

class TrackTable(DragMixin,QTableWidget):pass
class PlaylistList(DragMixin,QListWidget):pass

class HoverNavigation(QListWidget):
    def __init__(self):
        super().__init__();self.setAcceptDrops(True);self.target=-1
        self.timer=QTimer(self);self.timer.setSingleShot(True);self.timer.setInterval(550)
        self.timer.timeout.connect(lambda:self.setCurrentRow(self.target) if self.target>=0 else None)
    def dragEnterEvent(self,event):
        if decode(event.mimeData()):event.acceptProposedAction()
        else:event.ignore()
    def dragMoveEvent(self,event):
        if not decode(event.mimeData()):event.ignore();return
        index=self.row(self.itemAt(event.position().toPoint()))
        if index!=self.target:self.target=index;self.timer.start()
        event.acceptProposedAction()
    def dragLeaveEvent(self,event):self.timer.stop();self.target=-1;event.accept()
    def dropEvent(self,event):self.timer.stop();event.ignore()

class HoverTabs(QObject):
    def __init__(self,tabs):
        super().__init__(tabs);self.tabs=tabs;self.target=-1;tabs.tabBar().setAcceptDrops(True);tabs.tabBar().installEventFilter(self)
        self.timer=QTimer(self);self.timer.setSingleShot(True);self.timer.setInterval(550);self.timer.timeout.connect(lambda:tabs.setCurrentIndex(self.target) if self.target>=0 else None)
    def eventFilter(self,obj,event):
        if event.type() in (QEvent.Type.DragEnter,QEvent.Type.DragMove) and decode(event.mimeData()):
            index=obj.tabAt(event.position().toPoint())
            if index!=self.target:self.target=index;self.timer.start()
            event.acceptProposedAction();return True
        if event.type() in (QEvent.Type.DragLeave,QEvent.Type.Drop):self.timer.stop();self.target=-1
        return False
