import json
from PySide6.QtCore import Qt,QPoint,QPointF,QMimeData,QEvent
from PySide6.QtGui import QDragEnterEvent,QDragMoveEvent,QDropEvent,QMouseEvent,QDrag
from PySide6.QtWidgets import QTableWidgetItem,QApplication
from PySide6.QtTest import QTest
from dj_library.dragdrop import TrackTable,MIME,decode
from test_round6 import app,pump_until
from test_rekordbox import rb


def table(rows=6):
    app();view=TrackTable(rows,2);view.resize(500,320)
    view.setSelectionBehavior(view.SelectionBehavior.SelectRows)
    view.setSelectionMode(view.SelectionMode.ExtendedSelection)
    view.setDragEnabled(True);view.setAcceptDrops(True)
    for i in range(rows):
        for col in range(2):view.setItem(i,col,QTableWidgetItem(str(i)))
    view.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen);view.show();app().processEvents()
    return view


def mime():
    data=QMimeData();data.setData(MIME,json.dumps({'kind':'queue','jobs':['one']}).encode());return data


def test_real_mouse_gesture_starts_qt_drag_and_preserves_selection(monkeypatch):
    """Mouse viewport events reach startDrag; replace the OS loop for deterministic testing."""
    view=table();view.selectRow(0)
    view.selectionModel().select(view.model().index(1,0),view.selectionModel().SelectionFlag.Select|view.selectionModel().SelectionFlag.Rows)
    seen=[]
    view.drag_payload=lambda:dict(kind='queue',jobs=[i.row() for i in view.selectionModel().selectedRows()])
    from dj_library import dragdrop
    class ObservedDrag(QDrag):
        def exec(self,actions,default):
            seen.append((decode(self.mimeData()),default))
            return Qt.DropAction.IgnoreAction
    monkeypatch.setattr(dragdrop,'QDrag',ObservedDrag)
    start=view.visualItemRect(view.item(0,0)).center()
    QTest.mousePress(view.viewport(),Qt.MouseButton.LeftButton,Qt.KeyboardModifier.NoModifier,start)
    end=start+QPoint(0,60)
    move=QMouseEvent(QEvent.Type.MouseMove,QPointF(end),QPointF(view.viewport().mapToGlobal(end)),Qt.MouseButton.NoButton,Qt.MouseButton.LeftButton,Qt.KeyboardModifier.NoModifier)
    QApplication.sendEvent(view.viewport(),move)
    QTest.mouseRelease(view.viewport(),Qt.MouseButton.LeftButton,Qt.KeyboardModifier.NoModifier,end)
    assert seen and seen[0][0]['jobs']==[0,1]
    assert seen[0][1]==Qt.DropAction.MoveAction
    view.close()


def test_viewport_drag_events_show_between_row_line_and_drop_after_hovered_row():
    view=table();data=mime();drops=[]
    view.drop_tracks=lambda payload,point:drops.append(view.insertion_row(point)) or True
    rect=view.visualItemRect(view.item(2,0));point=QPoint(rect.center().x(),rect.bottom()-1)
    enter=QDragEnterEvent(point,Qt.DropAction.MoveAction,data,Qt.MouseButton.LeftButton,Qt.KeyboardModifier.NoModifier)
    move=QDragMoveEvent(point,Qt.DropAction.MoveAction,data,Qt.MouseButton.LeftButton,Qt.KeyboardModifier.NoModifier)
    QApplication.sendEvent(view.viewport(),enter);QApplication.sendEvent(view.viewport(),move)
    assert enter.isAccepted() and move.isAccepted() and view.insertion_row(point)==3
    assert view._drag_point==point
    image=view.viewport().grab().toImage();scale=image.devicePixelRatio()
    assert image.pixelColor(round(5*scale),round(view.rowViewportPosition(3)*scale))==view.palette().highlight().color()
    drop=QDropEvent(QPointF(point),Qt.DropAction.MoveAction,data,Qt.MouseButton.LeftButton,Qt.KeyboardModifier.NoModifier)
    QApplication.sendEvent(view.viewport(),drop)
    assert drop.isAccepted() and drops==[3] and view._drag_point is None
    view.close()


def test_drag_near_viewport_edge_autoscrolls_without_changing_selection():
    view=table(80);view.selectRow(0);view.drop_tracks=lambda *_:True;data=mime()
    point=QPoint(50,view.viewport().height()-3)
    QApplication.sendEvent(view.viewport(),QDragEnterEvent(point,Qt.DropAction.MoveAction,data,Qt.MouseButton.LeftButton,Qt.KeyboardModifier.NoModifier))
    QApplication.sendEvent(view.viewport(),QDragMoveEvent(point,Qt.DropAction.MoveAction,data,Qt.MouseButton.LeftButton,Qt.KeyboardModifier.NoModifier))
    pump_until(lambda:view.verticalScrollBar().value()>0,2)
    assert [i.row() for i in view.selectionModel().selectedRows()]==[0]
    view._stop_hover();view.close()



def test_queue_drop_event_moves_block_after_last_row_and_keeps_history(tmp_path):
    from dj_library.ui import Window
    from test_round10 import queue,select
    app();window=Window(tmp_path/'db');window.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen);window.show()
    ids=queue(window.catalogue,4);window.refresh_downloads();window.navigation.setCurrentRow(1);window.download_tabs.setCurrentIndex(1)
    app().processEvents();view=window.downloads_table;select(view,[0,1]);payload=window.queue_drag_data()
    data=QMimeData();data.setData(MIME,json.dumps(payload).encode())
    rect=view.visualItemRect(view.item(3,0));point=QPoint(rect.center().x(),rect.bottom()-1)
    QApplication.sendEvent(view.viewport(),QDragEnterEvent(point,Qt.DropAction.MoveAction,data,Qt.MouseButton.LeftButton,Qt.KeyboardModifier.NoModifier))
    drop=QDropEvent(QPointF(point),Qt.DropAction.MoveAction,data,Qt.MouseButton.LeftButton,Qt.KeyboardModifier.NoModifier)
    QApplication.sendEvent(view.viewport(),drop)
    assert drop.isAccepted() and [j['id'] for j in window.catalogue.download_jobs()]==ids[2:]+ids[:2]
    assert {r['id'] for r in window.selected_downloads()}==set(ids[:2])
    window.close()


def test_own_playlist_drop_event_reorders_using_after_half(tmp_path):
    from dj_library.ui import Window
    from test_catalogue import audio
    from test_round10 import select
    app();window=Window(tmp_path/'db');window.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen);window.show()
    for i in range(3):audio(tmp_path/'music'/f'{i}.wav',8000+i*1000)
    window.catalogue.add_root(tmp_path/'music');window.catalogue.scan()
    id_=window.catalogue.create_playlist('Set');window.catalogue.add_entries(id_,[f['recording_id'] for f in window.catalogue.files()])
    window.playlist_id=id_;window.refresh_playlists();window.navigation.setCurrentRow(3);app().processEvents()
    view=window.entries_table;select(view,[0]);before=[r['id'] for r in window.catalogue.entries(id_)]
    data=QMimeData();data.setData(MIME,json.dumps(view.drag_payload()).encode())
    rect=view.visualItemRect(view.item(2,0));point=QPoint(rect.center().x(),rect.bottom()-1)
    QApplication.sendEvent(view.viewport(),QDragEnterEvent(point,Qt.DropAction.MoveAction,data,Qt.MouseButton.LeftButton,Qt.KeyboardModifier.NoModifier))
    drop=QDropEvent(QPointF(point),Qt.DropAction.MoveAction,data,Qt.MouseButton.LeftButton,Qt.KeyboardModifier.NoModifier)
    QApplication.sendEvent(view.viewport(),drop)
    assert drop.isAccepted() and [r['id'] for r in window.catalogue.entries(id_)]==before[1:]+before[:1]
    assert [i.row() for i in view.selectionModel().selectedRows()]==[2]
    window.close()


def test_native_playlist_drop_uses_same_insertion_and_restores_song_selection(rb,tmp_path):
    from dj_library.ui import Window,ROLE
    from test_round10 import select
    app();playlist=rb.create_playlist('Drag set');rb.add_tracks(playlist,['1','2'])
    before=rb.source.read_bytes();songs=next(p['songs'] for p in rb.view()['playlists'] if p['id']==playlist)
    window=Window(rb.catalogue.workspace);window.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen);window.show()
    panel=window.rb_panel;panel.backend=rb;panel.loaded(rb.view())
    window.navigation.setCurrentRow(3);window.playlist_tabs.setCurrentIndex(1)
    from PySide6.QtWidgets import QTreeWidgetItemIterator
    iterator=QTreeWidgetItemIterator(panel.tree)
    while iterator.value():
        if iterator.value().data(0,ROLE)==playlist:panel.tree.setCurrentItem(iterator.value());break
        iterator+=1
    app().processEvents();view=panel.table;select(view,[0])
    data=QMimeData();data.setData(MIME,json.dumps(view.drag_payload()).encode())
    rect=view.visualItemRect(view.item(1,0));point=QPoint(rect.center().x(),rect.bottom()-1)
    QApplication.sendEvent(view.viewport(),QDragEnterEvent(point,Qt.DropAction.MoveAction,data,Qt.MouseButton.LeftButton,Qt.KeyboardModifier.NoModifier))
    drop=QDropEvent(QPointF(point),Qt.DropAction.MoveAction,data,Qt.MouseButton.LeftButton,Qt.KeyboardModifier.NoModifier)
    QApplication.sendEvent(view.viewport(),drop);pump_until(lambda:window.job is None)
    assert drop.isAccepted()
    assert [s['id'] for s in next(p['songs'] for p in rb.view()['playlists'] if p['id']==playlist)]==[songs[1]['id'],songs[0]['id']]
    assert [r['song_id'] for r in panel.selected()]==[songs[0]['id']]
    assert rb.source.read_bytes()==before
    window.close()
