"""One writer per database, with a local activation channel for subsequent launches."""
import hashlib,os
from pathlib import Path
from PySide6.QtCore import QObject,Signal,QLockFile,QTimer,Qt
from PySide6.QtNetwork import QLocalServer,QLocalSocket
from PySide6.QtWidgets import QApplication

def server_name(path):
    return 'ZotiBox-'+hashlib.sha256(os.path.normcase(str(Path(path).resolve())).encode()).hexdigest()[:32]

def notify_existing(path,timeout=600):
    socket=QLocalSocket();socket.connectToServer(server_name(path))
    if not socket.waitForConnected(timeout):return False
    if os.name=='nt':
        try:
            import ctypes
            pid=int((Path(path)/'.application.lock').read_text().splitlines()[0])
            ctypes.windll.user32.AllowSetForegroundWindow(pid)
        except (OSError,ValueError,IndexError):pass
    socket.write(b'activate\n');socket.flush();socket.waitForBytesWritten(timeout);socket.disconnectFromServer()
    return True

def acquire_lock(path):
    path=Path(path).resolve();path.mkdir(parents=True,exist_ok=True)
    lock=QLockFile(str(path/'.application.lock'));lock.setStaleLockTime(0)
    # Qt checks the owning PID; disabling age expiry avoids stealing a slow live launch.
    if not lock.tryLock(0):
        info=lock.getLockInfo();pid=info[1] if info and info[0] else 'unknown'
        raise ValueError(f'This database is already open (process {pid}). Relaunch ZotiBox to restore its window. If it is stopping, allow it to finish before reopening.')
    return lock

def restore_window(window):
    if window.testAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen):return
    window.setWindowState(window.windowState() & ~Qt.WindowState.WindowMinimized)
    if not window.isVisible():window.showMaximized()
    screen=QApplication.screenAt(window.frameGeometry().center())
    if screen is None and QApplication.primaryScreen():
        window.move(QApplication.primaryScreen().availableGeometry().topLeft());window.showMaximized()
    window.raise_();window.activateWindow()
    if os.name=='nt':
        import ctypes
        from ctypes import wintypes
        user=ctypes.windll.user32;handle=int(window.winId())
        user.ShowWindowAsync.argtypes=[wintypes.HWND,ctypes.c_int]
        user.IsIconic.argtypes=[wintypes.HWND]
        user.ShowWindowAsync(handle,9 if user.IsIconic(handle) else 5)
        show=user.SetForegroundWindow;show.argtypes=[wintypes.HWND];show(handle)

def restore_legacy(path):
    """Older releases have a lock but no IPC server; restore their exact owning window."""
    if os.name!='nt':return False
    try:
        import ctypes,psutil
        from ctypes import wintypes
        pid=int((Path(path)/'.application.lock').read_text().splitlines()[0])
        if psutil.Process(pid).name().casefold()!='zotibox.exe':return False
        user=ctypes.windll.user32;found=[]
        callback=ctypes.WINFUNCTYPE(wintypes.BOOL,wintypes.HWND,wintypes.LPARAM)
        def visit(hwnd,param):
            owner=wintypes.DWORD();user.GetWindowThreadProcessId(hwnd,ctypes.byref(owner))
            title=ctypes.create_unicode_buffer(256);user.GetWindowTextW(hwnd,title,256)
            if owner.value==pid and title.value=='ZotiBox':found.append(hwnd)
            return True
        user.EnumWindows(callback(visit),0)
        if not found:return False
        user.AllowSetForegroundWindow(pid)
        user.ShowWindowAsync.argtypes=[wintypes.HWND,ctypes.c_int]
        user.SetForegroundWindow.argtypes=[wintypes.HWND]
        user.ShowWindowAsync(found[0],9 if user.IsIconic(found[0]) else 5);user.SetForegroundWindow(found[0]);return True
    except Exception:return False

class Instance(QObject):
    activate=Signal()
    def __init__(self,path,parent=None):
        super().__init__(parent);self.path=Path(path).resolve();self.lock=None;self.server=None;self.window=None
        self.activate.connect(self.restore)
    def acquire(self):
        if notify_existing(self.path):return False
        self.lock=acquire_lock(self.path)
        self.server=QLocalServer(self);self.server.setSocketOptions(QLocalServer.SocketOption.UserAccessOption)
        QLocalServer.removeServer(server_name(self.path)) # Only the proven lock owner removes a stale endpoint.
        if not self.server.listen(server_name(self.path)):
            self.lock.unlock();raise RuntimeError('Cannot create the local activation channel: '+self.server.errorString())
        self.server.newConnection.connect(self.connected);return True
    def connected(self):
        while self.server.hasPendingConnections():
            socket=self.server.nextPendingConnection()
            # Connecting to this user-scoped endpoint is the only supported request.
            # No file paths, SQL or other commands are read or executed.
            self.activate.emit()
            if socket:socket.close();socket.deleteLater()
    def restore(self):
        if self.window:restore_window(self.window)
    def attach(self,window):self.window=window
    def rebind(self,path,lock):
        if self.server:self.server.close()
        self.path=Path(path).resolve();self.lock=lock
        QLocalServer.removeServer(server_name(self.path))
        if not self.server.listen(server_name(self.path)):raise RuntimeError(self.server.errorString())
    def close(self):
        if self.server:self.server.close()
        if self.lock:self.lock.unlock()
