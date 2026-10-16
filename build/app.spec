from pathlib import Path
import os
from PyInstaller.utils.hooks import collect_submodules,collect_data_files,collect_dynamic_libs,copy_metadata

root = Path(SPECPATH).parent
a = Analysis(
    [str(root / 'desktop' / 'launcher.py')],
    pathex=[str(root / 'desktop')],
    binaries=collect_dynamic_libs('pyogg') + collect_dynamic_libs('sqlcipher3') + [(str(path),'ffmpeg') for path in (root/'tools'/'ffmpeg').glob('*') if path.suffix.lower() in {'.exe','.dll'}],
    datas=[(str(root / 'THIRD-PARTY-NOTICES.md'), '.'),
           (str(root / 'build' / 'generated' / 'licenses'), 'licenses'),
           (str(root / 'build' / 'generated' / 'ZotiBox-Source-0.10.0.zip'), '.'),
           (str(root/'tools'/'ffmpeg'/'LICENSE.txt'),'licenses/FFmpeg'),
           (str(root/'desktop'/'dj_library'/'resources'),'dj_library/resources')]
          + copy_metadata('zotify') + copy_metadata('librespot') + copy_metadata('pyrekordbox') + collect_data_files('pyogg'),
    hiddenimports=collect_submodules('zotify') + collect_submodules('librespot') + ['sqlalchemy.dialects.sqlite.pysqlcipher','sqlalchemy.dialects.sqlite.pysqlite'],
    hookspath=[],
    excludes=['PySide6.QtWebEngineCore', 'PySide6.QtWebEngineWidgets', 'PySide6.QtWebEngineQuick', 'PySide6.QtQml', 'PySide6.QtQuick', 'PySide6.QtPdf', 'PySide6.QtPdfWidgets', 'pytest'],
    noarchive=False,
)
# Qt uses Windows' system ICU. Do not pick up Poppler/Conda's incompatible ICU
# from a developer machine's PATH and ship it in place of the Windows API.
a.binaries = [entry for entry in a.binaries
              if Path(entry[0]).name.lower() != 'icuuc.dll'
              and not Path(entry[0]).name.lower().startswith('icudt')]
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name='ZotiBox',
          debug=False, bootloader_ignore_signals=False, strip=False, upx=False,
          icon=str(root / 'build' / 'generated' / 'app.ico'),
          console=os.environ.get('DJ_LIBRARY_BUILD_CONSOLE') == '1')
worker = EXE(pyz,a.scripts,[],exclude_binaries=True,name='ZotiBox Worker',console=True,debug=False,strip=False,upx=False,icon=str(root/'build'/'generated'/'app.ico'))
coll = COLLECT(exe, worker, a.binaries, a.datas, strip=False, upx=False, name='ZotiBox')
