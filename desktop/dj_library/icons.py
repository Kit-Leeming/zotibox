"""Small outline icons, drawn natively so colours follow the theme."""
from PySide6.QtCore import Qt,QPointF,QRectF
from PySide6.QtGui import QImage,QIcon,QPixmap,QPainter,QPen,QPolygonF

def icon(name,colour):
    image=QImage(40,40,QImage.Format.Format_ARGB32)
    image.fill(Qt.GlobalColor.transparent)
    p=QPainter(image)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.scale(2,2)
    p.setPen(QPen(colour,1.6,Qt.PenStyle.SolidLine,Qt.PenCapStyle.RoundCap,Qt.PenJoinStyle.RoundJoin))
    p.setBrush(Qt.BrushStyle.NoBrush)
    def line(x1,y1,x2,y2):p.drawLine(QPointF(x1,y1),QPointF(x2,y2))
    if name in {"SP_DirIcon","SP_DirOpenIcon"}:
        p.drawPolygon(QPolygonF([QPointF(2,6),QPointF(8,6),QPointF(10,8),QPointF(18,8),QPointF(18,17),QPointF(2,17)]))
    elif name=="SP_ComputerIcon":
        p.drawPolyline(QPolygonF([QPointF(2,9),QPointF(10,2),QPointF(18,9)]))
        p.drawPolyline(QPolygonF([QPointF(4,8),QPointF(4,18),QPointF(16,18),QPointF(16,8)]))
        p.drawRect(QRectF(8,12,4,6))
    elif name in {"SP_FileDialogListView","SP_FileDialogContentsView"}:
        for y in [5,10,15]:
            p.drawEllipse(QPointF(3,y),.7,.7)
            line(7,y,18,y)
    elif name=="SP_FileDialogDetailedView":
        p.drawEllipse(QRectF(5,5,10,10))
        p.drawEllipse(QRectF(8,8,4,4))
        for x1,y1,x2,y2 in [(10,1,10,4),(10,16,10,19),(1,10,4,10),(16,10,19,10),(3,3,5,5),(15,15,17,17),(3,17,5,15),(15,5,17,3)]:
            line(x1,y1,x2,y2)
    elif name=="SP_BrowserReload":
        p.drawArc(QRectF(3,3,14,14),45*16,280*16)
        p.drawPolyline(QPolygonF([QPointF(13,2),QPointF(17,4),QPointF(15,8)]))
    elif name in {"SP_ArrowUp","SP_ArrowDown","SP_ArrowRight"}:
        if name=="SP_ArrowDown":
            line(10,2,10,14)
            p.drawPolyline(QPolygonF([QPointF(5,9),QPointF(10,14),QPointF(15,9)]))
            p.drawPolyline(QPolygonF([QPointF(3,14),QPointF(3,18),QPointF(17,18),QPointF(17,14)]))
        elif name=="SP_ArrowUp":
            line(10,18,10,3)
            p.drawPolyline(QPolygonF([QPointF(5,8),QPointF(10,3),QPointF(15,8)]))
        else:
            line(2,10,17,10)
            p.drawPolyline(QPolygonF([QPointF(12,5),QPointF(17,10),QPointF(12,15)]))
    elif name=="SP_DialogCancelButton":
        line(5,5,15,15);line(15,5,5,15)
    elif name=="SP_MediaPlay":
        p.drawPolygon(QPolygonF([QPointF(6,3),QPointF(17,10),QPointF(6,17)]))
    elif name=="SP_MediaPause":
        line(6,3,6,17);line(14,3,14,17)
    elif name=="SP_DialogSaveButton":
        p.drawRect(QRectF(3,2,14,16));p.drawRect(QRectF(6,2,8,5));p.drawRect(QRectF(6,12,8,6))
    elif name=="SP_DesktopIcon":
        p.drawRoundedRect(QRectF(2,3,16,11),1,1);line(10,14,10,18);line(6,18,14,18)
    elif name in {"SP_MessageBoxInformation","SP_FileDialogInfoView"}:
        p.drawEllipse(QRectF(2,2,16,16));line(10,9,10,14);p.drawPoint(QPointF(10,6))
    else:
        p.drawRect(QRectF(4,2,12,16));line(7,7,13,7);line(7,11,13,11)
    p.end()
    pixmap=QPixmap.fromImage(image)
    pixmap.setDevicePixelRatio(2)
    return QIcon(pixmap)
