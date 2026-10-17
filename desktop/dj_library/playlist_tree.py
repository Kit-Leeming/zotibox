"""Playlist hierarchy with list-compatible access for existing actions."""
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QTreeWidget,QTreeWidgetItem,QTreeWidgetItemIterator
from .dragdrop import DragMixin
ROLE=Qt.ItemDataRole.UserRole

class PlaylistItem(QTreeWidgetItem):
    def data(self,*args):return super().data(0,args[0]) if len(args)==1 else super().data(*args)
    def setData(self,*args):return super().setData(0,*args) if len(args)==2 else super().setData(*args)

class PlaylistTree(DragMixin,QTreeWidget):
    def __init__(self):
        super().__init__();self.setHeaderHidden(True);self.setDragEnabled(True);self.setAcceptDrops(True);self.setDropIndicatorShown(True);self.setSelectionMode(self.SelectionMode.ExtendedSelection)
    def playlist_items(self):
        iterator=QTreeWidgetItemIterator(self);items=[]
        while iterator.value():
            item=iterator.value()
            if item.data(ROLE):items.append(item)
            iterator+=1
        return items
    def count(self):return len(self.playlist_items())
    def item(self,index):return self.playlist_items()[index]
    def setCurrentRow(self,index):self.setCurrentItem(self.item(index))
