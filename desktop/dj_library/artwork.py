"""Bounded embedded-cover cache; never changes audio tags or fetches remote images."""
from functools import lru_cache
import base64
from pathlib import Path
from mutagen import File
from mutagen.flac import Picture
from PySide6.QtCore import Qt
from PySide6.QtGui import QImage, QIcon, QPixmap

@lru_cache(maxsize=256)
def cover(path, size, modified):
    try:
        audio = File(path)
        data = None
        if getattr(audio, "pictures", None):
            data = audio.pictures[0].data
        elif getattr(audio, "tags", None):
            tags = audio.tags
            if hasattr(tags, "getall") and tags.getall("APIC"):
                data = tags.getall("APIC")[0].data
            elif tags.get("covr"):
                data = bytes(tags["covr"][0])
            elif tags.get("metadata_block_picture"):
                data = Picture(base64.b64decode(tags["metadata_block_picture"][0])).data
        if data and len(data) <= 8 * 1024 * 1024:
            image = QImage.fromData(data)
            if not image.isNull():
                return QIcon(QPixmap.fromImage(image.scaled(96,96,Qt.AspectRatioMode.KeepAspectRatio,Qt.TransformationMode.SmoothTransformation)))
    except Exception:
        pass
    return QIcon()

def thumbnail(path, fallback):
    if path:
        try:
            stat = Path(path).stat()
            result = cover(str(path), stat.st_size, stat.st_mtime_ns)
            if not result.isNull(): return result
        except OSError:
            pass
    return fallback
