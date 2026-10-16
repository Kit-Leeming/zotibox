import json
import time
import os,tempfile,traceback
from datetime import datetime,timezone
import argparse
import sys
from pathlib import Path
from PySide6.QtCore import Qt, QSettings, QTimer
from PySide6.QtGui import QFont,QPixmap,QColor
from PySide6.QtWidgets import QApplication, QMessageBox, QSplashScreen
from .instance import Instance,restore_legacy

def application_folder():
    return Path(sys.executable).parent if getattr(sys,"frozen",False) else Path(__file__).parents[2]

def log_startup(message):
    try:
        path=Path(tempfile.gettempdir())/"ZotiBox-startup.log"
        if path.exists() and path.stat().st_size>256000:path.replace(path.with_suffix(".previous.log"))
        with path.open("a",encoding="utf-8") as output:output.write(datetime.now(timezone.utc).isoformat()+f" PID {os.getpid()} "+message+"\n")
    except OSError:pass

def main():
    started=time.monotonic()
    parser=argparse.ArgumentParser(description="ZotiBox desktop")
    parser.add_argument("--workspace",type=Path)
    parser.add_argument("--client-id")
    parser.add_argument("--smoke-test",type=Path)
    parser.add_argument("--smoke-track",type=Path)
    args=parser.parse_args()
    if args.smoke_track and not args.smoke_test:parser.error("--smoke-track requires --smoke-test")
    app=QApplication(sys.argv[:1])
    app.setApplicationName("ZotiBox")
    app.setOrganizationName("ZotiBox")
    app.setFont(QFont("Segoe UI",10))
    remembered=QSettings().value("database","") if not args.smoke_test else ""
    folder=args.workspace or (Path(remembered) if remembered else application_folder()/"ZotiBox Database")
    if not args.smoke_test:log_startup("Launch requested")
    instance=None
    splash=None
    try:
        if not args.smoke_test:
            pixmap=QPixmap(400,150);pixmap.fill(QColor('#202327'));splash=QSplashScreen(pixmap)
            splash.showMessage("Starting ZotiBox…",Qt.AlignmentFlag.AlignCenter,QColor('#e5e9ee'));splash.show();app.processEvents()
        instance=Instance(folder,app)
        if not instance.acquire():
            if splash:splash.close()
            if not args.smoke_test:log_startup("Activation sent to existing instance")
            return 0
        from .ui import Window
        window=Window(folder,remember=not bool(args.smoke_test),workspace_lock=instance.lock,defer_startup=not bool(args.smoke_test))
        instance.attach(window);window.instance=instance
        client=args.client_id or (QSettings().value("setup_client_id","") if not args.smoke_test else "")
        if client and not window.catalogue.get_setting("spotify_client_id",""):
            window.catalogue.set_setting("spotify_client_id",client)
            window.client_edit.setText(client)
            window.reset_integrations()
    except Exception as exc:
        if splash:splash.close()
        if not args.smoke_test and (instance is None or instance.lock is None) and restore_legacy(folder):return 0
        if not args.smoke_test:log_startup("Startup failed: "+traceback.format_exc())
        if instance:instance.close()
        if args.smoke_test:
            args.smoke_test.mkdir(parents=True,exist_ok=True)
            (args.smoke_test/"startup-error.txt").write_text(str(exc),encoding="utf-8")
        else:
            QMessageBox.critical(None,"Cannot open ZotiBox Database",str(exc))
        return 1
    if args.smoke_test:
        window.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen)
        window.resize(1500,900)
        window.show()
        deadline=time.monotonic()+12
        if args.smoke_track:
            window.player.audio.setVolume(0)
            window.player.load(args.smoke_track,"Muted player validation",autoplay=True)
        def capture():
            if args.smoke_track:
                ready=not window.player.jobs and bool(window.player.wave.peaks) and window.player.media.position()>100
                if not ready and time.monotonic()<deadline:QTimer.singleShot(100,capture);return
                args.smoke_test.mkdir(parents=True,exist_ok=True)
                (args.smoke_test/"player.json").write_text(json.dumps({"ready":ready,"duration_ms":window.player.media.duration(),"position_ms":window.player.media.position(),"waveform_peaks":len(window.player.wave.peaks),"error":window.player.media.errorString()}),encoding="utf-8")
                if not ready:window.player.shutdown();app.exit(1);return
                window.player.media.setPosition(1000)
            args.smoke_test.mkdir(parents=True,exist_ok=True)
            for index,name in enumerate(["dashboard","downloads","local-music","playlists","settings"]):
                window.navigation.setCurrentRow(index)
                app.processEvents()
                window.grab().save(str(args.smoke_test/f"{name}.png"))
            window.player.shutdown()
            app.quit()
        QTimer.singleShot(300,capture)
    else:
        QSettings().setValue("database",str(window.catalogue.workspace))
        window.showMaximized()
        if splash:splash.finish(window)
        app.processEvents()
        log_startup(f"Main window visible after {time.monotonic()-started:.2f}s")
    result=app.exec()
    instance.close()
    return result

if __name__=="__main__":
    raise SystemExit(main())
