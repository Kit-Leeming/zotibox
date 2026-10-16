import time

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QFileDialog, QInputDialog

from dj_library.ui import Window
from dj_library.catalogue import Catalogue
from test_catalogue import audio


@pytest.fixture(scope="module")
def application():
    return QApplication.instance() or QApplication([])


def test_desktop_scan_playlist_and_export_workflow(application, tmp_path, monkeypatch):
    window = Window(tmp_path / "workspace")
    window.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen)
    window.show()
    music = tmp_path / "music"
    audio(music / "First.wav", 8000)
    audio(music / "Second.wav", 16000)
    window.catalogue.add_root(music)
    window.scan()
    deadline = time.monotonic() + 10
    while (window.job or window.scan_job) and time.monotonic() < deadline:
        application.processEvents()
        time.sleep(0.01)
    assert window.job is None
    assert window.files_table.topLevelItem(0).childCount() == 2
    monkeypatch.setattr(QInputDialog, "getText", lambda *args: ("Test set", True))
    window.new_playlist()
    monkeypatch.setattr("dj_library.ui.choose_playlist", lambda items,*args,**kwargs: items[0])
    window.files_table.selectAll()
    window.add_selected()
    assert window.entries_table.rowCount() == 2
    original = window.catalogue.entries(window.playlist_id)
    window.entries_table.selectRow(0)
    window.move_entry(1)
    assert window.catalogue.entries(window.playlist_id)[1]["id"] == original[0]["id"]
    destination = tmp_path / "set.m3u8"
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *args: (str(destination), ""))
    window.export_playlist()
    assert destination.read_text(encoding="utf-8").count("#EXTINF") == 2
    window.entries_table.selectRow(0)
    window.remove_entry()
    assert window.entries_table.rowCount() == 1
    assert len(list(music.glob("*.wav"))) == 2
    window.search.setText("Second")
    window.refresh_files()
    assert window.files_table.topLevelItem(0).childCount() == 1
    window.files_table.topLevelItem(0).child(0).setSelected(True)
    window.file_selection()
    assert "Second.wav" in window.details.text()
    window.close()


def test_workspace_lock_and_switch_preserve_current_workspace_on_failure(application, tmp_path):
    first = Window(tmp_path / "first")
    second = Window(tmp_path / "second")
    with pytest.raises(ValueError, match="already open"):
        second.switch_workspace(first.catalogue.workspace)
    assert second.catalogue.workspace == tmp_path / "second"
    first.close()
    second.switch_workspace(tmp_path / "first")
    assert second.catalogue.workspace == tmp_path / "first"
    second.close()


def test_theme_selection_is_continuous_across_columns(application,tmp_path):
    from PySide6.QtGui import QColor
    from dj_library.themes import make_theme
    window=Window(tmp_path/"workspace")
    window.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen)
    window.resize(1500,900)
    window.show()
    folder=tmp_path/"music"
    audio(folder/"Artist"/"First.wav")
    window.catalogue.add_root(folder)
    window.catalogue.scan()
    window.refresh()
    window.navigation.setCurrentRow(2)
    window.files_table.expandAll()
    item=window.files_table.topLevelItem(0).child(0).child(0)
    item.setSelected(True)
    window.apply_theme(make_theme({"accent":"#cc5599"}))
    application.processEvents()
    image=window.files_table.viewport().grab().toImage()
    ratio=image.devicePixelRatio()
    rect=window.files_table.visualItemRect(item)
    y=rect.top()+2
    # Sample either side of each column boundary, away from text.
    for column in range(1,6):
        boundary=window.files_table.columnViewportPosition(column)
        assert image.pixelColor(round((boundary-1)*ratio),round(y*ratio)).name()=="#cc5599"
        assert image.pixelColor(round((boundary+1)*ratio),round(y*ratio)).name()=="#cc5599"
    assert image.pixelColor(round(8*ratio),round(y*ratio)).name()=="#cc5599"
    assert window.search.isClearButtonEnabled()
    window.close()


def test_theme_harmonies_and_editor(application,tmp_path):
    from PySide6.QtGui import QColor
    from dj_library.themes import ThemeDialog,make_theme,harmony
    red=QColor("#ff0000")
    assert harmony(red,"Complementary")[1].name()=="#00ffff"
    assert len(harmony(red,"Triad"))==3
    dialog=ThemeDialog(make_theme())
    dialog.role.setCurrentIndex(dialog.role.findData("accent"))
    dialog.set_colour(QColor("#cc5599"))
    assert dialog.theme["selection"]["start"]=="#cc5599"
    dialog.role.setCurrentIndex(dialog.role.findData("background"))
    dialog.mode.setCurrentIndex(1)
    dialog.endpoint.setCurrentIndex(1)
    dialog.set_colour(QColor("#5577cc"))
    assert dialog.theme["background"]["end"]=="#5577cc"
    assert not dialog.theme["selection"]["gradient"]
    dialog.close()


def test_harmony_palette_does_not_drift_between_gradient_endpoints(application):
    from PySide6.QtGui import QColor
    from dj_library.themes import ThemeDialog,make_theme
    dialog=ThemeDialog(make_theme())
    dialog.set_colour(QColor("#aa2222"))
    dialog.mode.setCurrentIndex(1)
    dialog.harmony.setCurrentText("Triad")
    palette=[dialog.swatches.itemAt(i).widget().text() for i in range(dialog.swatches.count())]
    dialog.endpoint.setCurrentIndex(1)
    dialog.swatches.itemAt(1).widget().click()
    assert dialog.theme["background"]["start"]=="#aa2222"
    assert dialog.theme["background"]["end"]==palette[1]
    assert [dialog.swatches.itemAt(i).widget().text() for i in range(dialog.swatches.count())]==palette
    dialog.endpoint.setCurrentIndex(0)
    dialog.swatches.itemAt(2).widget().click()
    assert dialog.theme["background"]["end"]==palette[1]
    assert dialog.theme["background"]["start"]==palette[2]
    dialog.close()


def test_custom_bright_cyan_has_readable_derived_text():
    from dj_library.themes import valid_theme,make_theme
    theme=valid_theme(make_theme({"background":"#00ffff","accent":"#00ff00"}))
    assert theme["foreground"]["start"]=="#202431"
    assert theme["selection_text"]["start"]=="#111722"


def test_grouped_settings_theme_samples_and_queue_progress(application,tmp_path):
    from test_sync_downloads import remote
    window=Window(tmp_path/"database")
    assert any(window.settings_navigation.item(i).text()=='Zotify' for i in range(window.settings_navigation.count()))
    assert window.theme_samples.count()==12
    window.choose_theme_preset("Northern lights")
    assert window.theme["background"]["gradient"]
    assert window.theme["surface"]==window.theme["sidebar"]
    assert not window.theme["surface"]["gradient"]
    assert window.theme["selection"]==window.theme["accent"]
    track=remote(duration=120)
    window.catalogue.save_remote(track)
    job=window.catalogue.enqueue(track["id"])
    window.catalogue.update_download(job,state="running",progress=25)
    window.refresh_downloads()
    assert "1:30" in window.queue_summary.text()
    assert window.queue_progress.value()==250
    assert window.downloads_table.cellWidget(0,5) is None
    assert window.downloads_table.item(0,5).toolTip()=='25%'
    assert not window.queue_progress.isTextVisible()
    window.close()


def test_local_discovery_is_independent_of_spotify_task(application,tmp_path):
    import threading
    window=Window(tmp_path/"database")
    music=tmp_path/"music"
    audio(music/"First.wav")
    audio(music/"Second.wav")
    window.catalogue.add_root(music)
    release=threading.Event()
    window.start_job("Spotify scanning",lambda *args:release.wait(5),lambda _:None)
    window.scan()
    try:
        deadline=time.monotonic()+4
        while window.scan_job and time.monotonic()<deadline:
            application.processEvents();time.sleep(.01)
        assert window.scan_job is None
        assert window.job is not None
        assert len(window.catalogue.files())==2
        assert window.files_table.topLevelItem(0).childCount()==2
    finally:
        release.set()
        deadline=time.monotonic()+5
        while window.job and time.monotonic()<deadline:
            application.processEvents();time.sleep(.01)
        window.close()


def test_search_queue_download_sync_and_export_workflow(application,tmp_path,monkeypatch):
    from test_sync_downloads import tagged,remote
    window=Window(tmp_path/"database")
    window.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen)
    window.show()
    track=remote()
    snapshot={"id":"P"*22,"name":"Linked set","snapshot":"s1","complete":True,"tracks":[track,track]}
    class Service:
        def search(self,*args):
            return [{"id":track["id"],"name":track["title"],"artists":[{"name":track["artist"]}],"album":{"name":"Album"},"external_ids":{"isrc":track["isrc"]},"duration_ms":1000,"type":"track"}]
        def snapshot(self,*args):return snapshot
    window.spotify=Service()
    def wait_jobs():
        deadline=time.monotonic()+15
        while (window.job or window.download_job) and time.monotonic()<deadline:
            application.processEvents()
            time.sleep(.01)
        assert window.job is None and window.download_job is None
    window.download_search.setText("Track")
    window.find_downloads()
    wait_jobs()
    assert window.results_table.rowCount()==1
    window.results_table.selectRow(0)
    window.queue_results()
    wait_jobs()
    assert window.catalogue.download_jobs()[0]["state"]=="queued"
    monkeypatch.setattr(window.engine,"invoke",lambda stage,*args,**kwargs:(tagged(stage/"music"/"output.wav"),"OGG_VORBIS_320")[1])
    window.start_downloads()
    wait_jobs()
    assert window.catalogue.download_jobs()[0]["state"]=="complete"
    assert len(window.catalogue.files())==1
    window.sync_ids([snapshot["id"]])
    wait_jobs()
    assert window.entries_table.rowCount()==2
    assert not window.remove_entry_button.isEnabled()
    destination=tmp_path/"set.m3u8"
    monkeypatch.setattr(QFileDialog,"getSaveFileName",lambda *args:(str(destination),""))
    window.export_playlist()
    assert destination.read_text(encoding="utf-8").count("#EXTINF")==2
    snapshot.update(snapshot="s2",tracks=[])
    window.sync_selected()
    wait_jobs()
    assert window.entries_table.rowCount()==0
    assert len(window.catalogue.files())==1
    assert destination.read_text(encoding="utf-8")=="#EXTM3U\n"
    window.close()


def test_harmony_changes_do_not_obscure_colour_wheel(application):
    from PySide6.QtCore import QPoint
    from dj_library.themes import ThemeDialog,make_theme
    dialog=ThemeDialog(make_theme())
    dialog.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen)
    dialog.harmony.setCurrentText("Triad")
    dialog.show()
    application.processEvents()
    image=dialog.grab().toImage()
    point=dialog.wheel.mapTo(dialog,QPoint(dialog.wheel.width()//2,dialog.wheel.height()//2))
    ratio=image.devicePixelRatio()
    assert image.pixelColor(round(point.x()*ratio),round(point.y()*ratio)).name()=="#ffffff"
    dialog.close()
