"""Keep Qt windows and Windows shortcuts in the same taskbar application group."""
import ctypes
import os
import sys

APP_ID='ZotiBox.Desktop'


def identify():
    if os.name=='nt':ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(APP_ID)


def refresh_icon(window,path):
    """A pinned shortcut can override Qt's window icon; set its relaunch icon."""
    if os.name!='nt':return
    from ctypes import wintypes
    class GUID(ctypes.Structure):
        _fields_=[('a',wintypes.DWORD),('b',wintypes.WORD),('c',wintypes.WORD),('d',ctypes.c_ubyte*8)]
    def guid(value):
        result=GUID();ctypes.windll.ole32.CLSIDFromString(value,ctypes.byref(result));return result
    class Key(ctypes.Structure):_fields_=[('fmtid',GUID),('pid',wintypes.DWORD)]
    class Variant(ctypes.Structure):
        _fields_=[('vt',wintypes.WORD),('reserved',wintypes.WORD*3),('value',ctypes.c_void_p),('padding',ctypes.c_void_p)]
    pointer=ctypes.c_void_p();shell=ctypes.windll.shell32.SHGetPropertyStoreForWindow
    shell.argtypes=[wintypes.HWND,ctypes.POINTER(GUID),ctypes.POINTER(ctypes.c_void_p)];shell.restype=ctypes.c_long
    iid=guid('{886D8EEB-8CF2-4446-8D02-CDBA1DBDCF99}')
    if shell(int(window.winId()),ctypes.byref(iid),ctypes.byref(pointer))<0:return
    vtable=ctypes.cast(pointer,ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
    set_value=ctypes.WINFUNCTYPE(ctypes.c_long,ctypes.c_void_p,ctypes.POINTER(Key),ctypes.POINTER(Variant))(vtable[6])
    release=ctypes.WINFUNCTYPE(wintypes.ULONG,ctypes.c_void_p)(vtable[2]);fmtid=guid('{9F4C2855-9F79-4B39-A8D0-E1D42DE1D5F3}')
    try:
        properties=[(5,APP_ID),(3,str(path)+',0')]
        if getattr(sys,'frozen',False):properties.extend([(2,'"'+sys.executable+'"'),(4,'ZotiBox')])
        for pid,text in properties:
            value=ctypes.create_unicode_buffer(text);variant=Variant();variant.vt=31;variant.value=ctypes.cast(value,ctypes.c_void_p)
            set_value(pointer,ctypes.byref(Key(fmtid,pid)),ctypes.byref(variant))
        ctypes.WINFUNCTYPE(ctypes.c_long,ctypes.c_void_p)(vtable[7])(pointer)
    finally:release(pointer)
