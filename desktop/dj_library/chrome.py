"""Qt title controls and a single gradient canvas behind the interface."""
import ctypes
import sys
from PySide6.QtCore import Qt
from PySide6.QtGui import QPainter,QColor
from PySide6.QtWidgets import QWidget,QHBoxLayout,QLabel,QPushButton
from .themes import selection_brush


class Backdrop(QWidget):
    def __init__(self,get_theme):
        super().__init__();self.get_theme=get_theme

    def paintEvent(self,event):
        from PySide6.QtCore import QRectF
        painter=QPainter(self)
        painter.fillRect(self.rect(),selection_brush(self.get_theme()["background"],QRectF(self.rect())))


class TitleBar(QWidget):
    def __init__(self,window):
        super().__init__();self.window=window
        self.setFixedHeight(40);self.setObjectName("titlebar")
        row=QHBoxLayout(self);row.setContentsMargins(18,0,0,0);row.setSpacing(0)
        label=QLabel("ZotiBox");label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        row.addWidget(label);row.addStretch()
        for text,tip,callback in [("−","Minimise",window.showMinimized),("□","Maximise / restore",self.toggle),("×","Close",window.close)]:
            button=QPushButton(text);button.setFixedSize(48,40);button.setToolTip(tip)
            button.setObjectName("windowclose" if tip=="Close" else "windowcontrol")
            button.clicked.connect(callback);row.addWidget(button)
            if tip.startswith("Maximise"):self.maximise_button=button

    def toggle(self):
        self.window.showNormal() if self.window.isMaximized() else self.window.showMaximized()

    def mousePressEvent(self,event):
        if event.button()==Qt.MouseButton.LeftButton and self.window.windowHandle():
            self.window.windowHandle().startSystemMove()

    def mouseDoubleClickEvent(self,event):
        if event.button()==Qt.MouseButton.LeftButton:self.toggle()


def configure_frame(window):
    if sys.platform!="win32":return
    from ctypes import wintypes
    user=ctypes.windll.user32
    user.GetWindowLongW.argtypes=[wintypes.HWND,ctypes.c_int]
    user.SetWindowLongW.argtypes=[wintypes.HWND,ctypes.c_int,ctypes.c_long]
    hwnd=int(window.winId())
    style=user.GetWindowLongW(hwnd,-16)|0x00C00000|0x00040000|0x00030000|0x00080000
    user.SetWindowLongW(hwnd,-16,ctypes.c_long(style).value)
    user.SetWindowPos.argtypes=[wintypes.HWND,wintypes.HWND,ctypes.c_int,ctypes.c_int,ctypes.c_int,ctypes.c_int,ctypes.c_uint]
    user.SetWindowPos(hwnd,None,0,0,0,0,0x0020|0x0001|0x0002|0x0004|0x0010)


def resize_hit(window,message):
    """Keep native edge resizing and Windows snap behaviour with Qt's frameless bar."""
    if sys.platform!="win32":return None
    from ctypes import wintypes
    msg=wintypes.MSG.from_address(int(message))
    if msg.message==0x83 and msg.wParam:  # WM_NCCALCSIZE: draw our own caption.
        if window.isMaximized():
            class Params(ctypes.Structure):
                _fields_=[("rects",wintypes.RECT*3),("position",ctypes.c_void_p)]
            class MonitorInfo(ctypes.Structure):
                _fields_=[("size",wintypes.DWORD),("monitor",wintypes.RECT),("work",wintypes.RECT),("flags",wintypes.DWORD)]
            user=ctypes.windll.user32
            user.MonitorFromWindow.argtypes=[wintypes.HWND,wintypes.DWORD];user.MonitorFromWindow.restype=ctypes.c_void_p
            user.GetMonitorInfoW.argtypes=[ctypes.c_void_p,ctypes.POINTER(MonitorInfo)]
            info=MonitorInfo();info.size=ctypes.sizeof(info)
            if user.GetMonitorInfoW(user.MonitorFromWindow(int(window.winId()),2),ctypes.byref(info)):
                Params.from_address(msg.lParam).rects[0]=info.work
        return 0
    if msg.message in {0xA1,0xA2} and msg.wParam==9:  # Native maximise button / snap flyout.
        if msg.message==0xA2:window.titlebar.toggle()
        return 0
    if msg.message!=0x84:return None  # WM_NCHITTEST
    rect=wintypes.RECT();ctypes.windll.user32.GetWindowRect(int(window.winId()),ctypes.byref(rect))
    x=ctypes.c_short(msg.lParam&0xffff).value;y=ctypes.c_short((msg.lParam>>16)&0xffff).value
    from PySide6.QtCore import QPoint
    button=window.titlebar.maximise_button
    point=button.mapTo(window,QPoint(0,0));ratio=window.devicePixelRatioF()
    origin=wintypes.POINT()
    ctypes.windll.user32.ClientToScreen(int(window.winId()),ctypes.byref(origin))
    if origin.x+point.x()*ratio<=x<origin.x+(point.x()+button.width())*ratio and origin.y+point.y()*ratio<=y<origin.y+(point.y()+button.height())*ratio:return 9
    if window.isMaximized():return None
    edge=round(7*window.devicePixelRatioF())
    left=x<rect.left+edge;right=x>=rect.right-edge;top=y<rect.top+edge;bottom=y>=rect.bottom-edge
    if top and left:return 13
    if top and right:return 14
    if bottom and left:return 16
    if bottom and right:return 17
    if left:return 10
    if right:return 11
    if top:return 12
    if bottom:return 15
    return None
