"""Internal song drags, drop targets and delayed navigation while dragging."""
import json
from PySide6.QtCore import Qt,QMimeData,QTimer,QObject,QEvent
from PySide6.QtGui import QDrag,QPainter,QPen
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
    def restore_drag_selection(self,ids,key='id'):
        """Keep moved rows selected after a drop has rebuilt the table."""
        from PySide6.QtCore import QItemSelectionModel
        self.clearSelection();ids=set(ids)
        for row in range(self.rowCount()):
            item=self.item(row,0);data=item.data(Qt.ItemDataRole.UserRole) if item else None
            if data and data.get(key) in ids:
                self.selectionModel().select(self.model().index(row,0),QItemSelectionModel.SelectionFlag.Select|QItemSelectionModel.SelectionFlag.Rows)

    def insertion_row(self,point):
        """A track lands between rows, including below the final row."""
        row=self.rowAt(point.y())
        if row<0:return 0 if point.y()<0 else self.rowCount()
        return row+(point.y()>=self.rowViewportPosition(row)+self.rowHeight(row)/2)

    def _accept_drag(self,event):
        data=decode(event.mimeData())
        if not data or not getattr(self,'drop_tracks',None):event.ignore();return False
        moving=data.get('kind') in {'playlist','queue'} or bool(data.get('entries') or data.get('native_songs'))
        action=Qt.DropAction.MoveAction if moving else Qt.DropAction.CopyAction
        if not event.possibleActions()&action:action=event.proposedAction()
        event.setDropAction(action);event.accept();return True

    def startDrag(self,actions):
        callback=getattr(self,'drag_payload',None);payload=callback() if callback else None
        if not payload:return
        mime=QMimeData();mime.setData(MIME,json.dumps(payload).encode())
        drag=QDrag(self);drag.setMimeData(mime)
        action=Qt.DropAction.MoveAction if payload.get('kind') in {'playlist','queue'} or payload.get('entries') or payload.get('native_songs') else Qt.DropAction.CopyAction
        self._dragging=True
        try:drag.exec(Qt.DropAction.CopyAction|Qt.DropAction.MoveAction,action)
        finally:self._dragging=False;self._stop_hover()
    def dragEnterEvent(self,event):
        self._accept_drag(event)
    def dragMoveEvent(self,event):
        if not self._accept_drag(event):return
        self._drag_point=event.position().toPoint()
        if not hasattr(self,'_scroll_timer'):
            self._scroll_timer=QTimer(self);self._scroll_timer.setInterval(60)
            self._scroll_timer.timeout.connect(self._scroll_drag)
        self._scroll_timer.start();self.viewport().update()
        if event.isAccepted() and hasattr(self,'topLevelItemCount'):
            item=self.itemAt(event.position().toPoint())
            if item is not getattr(self,'_hover_item',None):
                self._hover_item=item
                if not hasattr(self,'_expand_timer'):
                    self._expand_timer=QTimer(self);self._expand_timer.setSingleShot(True);self._expand_timer.setInterval(650)
                    self._expand_timer.timeout.connect(self._expand_hover)
                self._expand_timer.start()
    def _scroll_drag(self):
        point=getattr(self,'_drag_point',None)
        if point is None:return
        margin=32;height=self.viewport().height();bar=self.verticalScrollBar()
        direction=-1 if point.y()<margin else 1 if point.y()>height-margin else 0
        if direction:bar.setValue(bar.value()+direction*max(1,bar.singleStep()))
        self.viewport().update()
    def paintEvent(self,event):
        super().paintEvent(event)
        point=getattr(self,'_drag_point',None)
        if point is None or not hasattr(self,'rowCount'):return
        row=self.insertion_row(point)
        y=self.rowViewportPosition(row) if row<self.rowCount() else self.rowViewportPosition(row-1)+self.rowHeight(row-1) if row else 1
        painter=QPainter(self.viewport());painter.setPen(QPen(self.palette().highlight().color(),3))
        painter.drawLine(0,max(1,y),self.viewport().width(),max(1,y));painter.end()
    def _expand_hover(self):
        item=getattr(self,'_hover_item',None)
        try:
            if item and item.treeWidget() is self:item.setExpanded(True)
        except RuntimeError:self._stop_hover() # A background refresh may have replaced the tree items.
    def _stop_hover(self):
        if hasattr(self,'_expand_timer'):self._expand_timer.stop()
        if hasattr(self,'_scroll_timer'):self._scroll_timer.stop()
        self._hover_item=None
        self._drag_point=None;self.viewport().update()
    def dragLeaveEvent(self,event):self._stop_hover();event.accept()
    def dropEvent(self,event):
        self._stop_hover()
        data=decode(event.mimeData());callback=getattr(self,'drop_tracks',None)
        if data and callback and callback(data,event.position().toPoint()):self._accept_drag(event)
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
