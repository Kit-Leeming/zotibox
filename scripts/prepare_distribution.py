"""Collect installed licence notices and archive this application's source."""
import importlib.metadata
import shutil
import sys
import zipfile
import struct
import subprocess
from pathlib import Path

root = Path(__file__).resolve().parent.parent
generated = root / "build" / "generated"
licences = generated / "licenses"
licences.mkdir(parents=True, exist_ok=True)
from PySide6.QtCore import QBuffer, QIODevice
from dj_library.branding import icon_image
pngs = []
for size in [16, 32, 48, 64, 128, 256]:
    buffer = QBuffer()
    buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    icon_image(size).save(buffer, "PNG")
    pngs.append((size, bytes(buffer.data())))
offset = 6 + 16 * len(pngs)
directory = bytearray(struct.pack("<HHH", 0, 1, len(pngs)))
for size, data in pngs:
    directory.extend(struct.pack("<BBBBHHII", size % 256, size % 256, 0, 0, 1, 32, len(data), offset))
    offset += len(data)
(generated / "app.ico").write_bytes(bytes(directory) + b"".join(data for size, data in pngs))
for name in ["mutagen", "PySide6", "PySide6_Essentials", "PySide6_Addons", "shiboken6", "pyinstaller", "zotify", "librespot", "requests", "Pillow", "pyogg", "pyrekordbox", "sqlcipher3-wheels", "sqlalchemy", "numpy", "psutil", "construct", "bidict"]:
    distribution = importlib.metadata.distribution(name)
    for relative in distribution.files or []:
        if any(part.lower().startswith(("license", "licence", "copying")) for part in Path(relative).parts):
            source = Path(distribution.locate_file(relative))
            if source.is_file():
                destination = licences / name / str(relative).replace("..", "_")
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(source, destination)
python_license = Path(sys.base_prefix) / "LICENSE.txt"
if python_license.is_file():
    shutil.copyfile(python_license, licences / "Python-LICENSE.txt")
output = generated / "ZotiBox-Source-0.15.3.zip"
with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
    tracked=subprocess.check_output(['git','ls-files','-z'],cwd=root).decode('utf-8').split('\0')
    for relative in tracked:
        path=root/relative
        if relative and path.is_file() and (relative.split('/')[0] in {'desktop','tests','scripts','docs','build'} or relative in {'pyproject.toml','requirements-build.txt','requirements-lock.txt','README.md','THIRD-PARTY-NOTICES.md'}):
            archive.write(path,relative)
print(f"Prepared licence notices and {output}")
