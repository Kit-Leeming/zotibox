"""Small native disc mark shared by the window and Windows executable."""
from PySide6.QtCore import Qt, QRectF
from PySide6.QtGui import QColor, QImage, QPainter, QPen


def icon_image(size=256, accent="#84c9a1"):
    image = QImage(size, size, QImage.Format.Format_ARGB32)
    image.fill(Qt.GlobalColor.transparent)
    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.scale(size / 256, size / 256)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor("#181a1d"))
    painter.drawRoundedRect(QRectF(8, 8, 240, 240), 42, 42)
    painter.setBrush(Qt.BrushStyle.NoBrush)
    painter.setPen(QPen(QColor(accent), 10))
    painter.drawEllipse(QRectF(48, 48, 160, 160))
    painter.setPen(QPen(QColor("#46554d"), 4))
    painter.drawEllipse(QRectF(68, 68, 120, 120))
    painter.drawEllipse(QRectF(84, 84, 88, 88))
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor(accent))
    painter.drawEllipse(QRectF(111, 111, 34, 34))
    painter.setBrush(QColor("#181a1d"))
    painter.drawEllipse(QRectF(123, 123, 10, 10))
    painter.end()
    return image


def save_icon(path, accent):
    import struct
    from PySide6.QtCore import QBuffer, QIODevice
    pngs = []
    for size in [16,32,48,64,128,256]:
        buffer = QBuffer()
        buffer.open(QIODevice.OpenModeFlag.WriteOnly)
        icon_image(size, accent).save(buffer, "PNG")
        pngs.append((size, bytes(buffer.data())))
    offset = 6 + 16 * len(pngs)
    header = bytearray(struct.pack("<HHH", 0, 1, len(pngs)))
    for size, data in pngs:
        header.extend(struct.pack("<BBBBHHII", size%256, size%256, 0, 0, 1, 32, len(data), offset))
        offset += len(data)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(bytes(header) + b"".join(data for _, data in pngs))
