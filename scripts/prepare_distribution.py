"""Collect installed licence notices and archive this application's source."""
import importlib.metadata
import shutil
import sys
import zipfile
import struct
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
output = generated / "ZotiBox-Source-0.8.0.zip"
with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
    for directory in ["desktop", "tests", "scripts", "docs", "build"]:
        for path in (root / directory).rglob("*"):
            if path.is_file() and not any(part in {"generated", "__pycache__", "dj_library_desktop.egg-info"} for part in path.relative_to(root).parts):
                archive.write(path, path.relative_to(root).as_posix())
    for name in ["pyproject.toml", "requirements-build.txt", "requirements-lock.txt", "README.md", "THIRD-PARTY-NOTICES.md"]:
        archive.write(root / name, name)
print(f"Prepared licence notices and {output}")
