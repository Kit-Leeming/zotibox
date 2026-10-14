import argparse
import sys
from pathlib import Path
from PySide6.QtCore import Qt, QSettings, QTimer
from PySide6.QtGui import QFont
from PySide6.QtWidgets import QApplication, QMessageBox
from .ui import Window

def application_folder():
    return Path(sys.executable).parent if getattr(sys,"frozen",False) else Path(__file__).parents[2]

def main():
    parser=argparse.ArgumentParser(description="ZotiBox desktop")
    parser.add_argument("--workspace",type=Path)
    parser.add_argument("--client-id")
    parser.add_argument("--smoke-test",type=Path)
    args=parser.parse_args()
    app=QApplication(sys.argv[:1])
    app.setApplicationName("ZotiBox")
    app.setOrganizationName("ZotiBox")
    app.setFont(QFont("Segoe UI",10))
    remembered=QSettings().value("database","") if not args.smoke_test else ""
    folder=args.workspace or (Path(remembered) if remembered else application_folder()/"ZotiBox Database")
    try:
        window=Window(folder,remember=not bool(args.smoke_test))
        client=args.client_id or (QSettings().value("setup_client_id","") if not args.smoke_test else "")
        if client and not window.catalogue.get_setting("spotify_client_id",""):
            window.catalogue.set_setting("spotify_client_id",client)
            window.client_edit.setText(client)
            window.reset_integrations()
    except Exception as exc:
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
        def capture():
            args.smoke_test.mkdir(parents=True,exist_ok=True)
            for index,name in enumerate(["dashboard","downloads","local-music","playlists","settings"]):
                window.navigation.setCurrentRow(index)
                app.processEvents()
                window.grab().save(str(args.smoke_test/f"{name}.png"))
            app.quit()
        QTimer.singleShot(300,capture)
    else:
        QSettings().setValue("database",str(window.catalogue.workspace))
        window.showMaximized()
    return app.exec()

if __name__=="__main__":
    raise SystemExit(main())
