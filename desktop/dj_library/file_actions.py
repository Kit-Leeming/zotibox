"""Explicit file removal goes to the Windows Recycle Bin, never permanent deletion."""
import ctypes
from pathlib import Path
import os

def recycle(path):
    if os.name != "nt": raise RuntimeError("Recycle Bin removal requires Windows")
    path = Path(path).absolute()
    if not path.is_file() or path.is_symlink(): raise ValueError("Select an existing regular music file")
    class Operation(ctypes.Structure):
        _fields_ = [("hwnd",ctypes.c_void_p),("wFunc",ctypes.c_uint),("pFrom",ctypes.c_wchar_p),
                    ("pTo",ctypes.c_wchar_p),("fFlags",ctypes.c_ushort),("aborted",ctypes.c_int),
                    ("mapping",ctypes.c_void_p),("title",ctypes.c_wchar_p)]
    # Shell may prompt if a volume cannot recycle; do not suppress its confirmation.
    operation = Operation(None,3,str(path)+"\0\0",None,0x40|0x4000,0,None,None)
    result = ctypes.windll.shell32.SHFileOperationW(ctypes.byref(operation))
    if result or operation.aborted or path.exists():
        raise RuntimeError("File removal was cancelled or could not complete")
