"""Иконка в трее Windows: только ctypes и стандартная библиотека.

    from app import tray
    icon = tray.start("http://[IP_ADDRESS]:7867", on_exit=my_stop)
    icon.stop()

Вся работа идёт в отдельном потоке: скрытое окно и цикл сообщений.
Вызывающий код не блокируется. stop() шлёт WM_CLOSE, окно удаляется,
иконка снимается через Shell_NotifyIcon(NIM_DELETE).
"""
from __future__ import annotations

import ctypes
import math
import os
import pathlib
import sys
import tempfile
import threading
import time
import traceback
import webbrowser

IS_WINDOWS = os.name == "nt"

TITLE = "Локальный агент"
DEFAULT_URL = "http://localhost:7867"

# сообщения окна
WM_USER = 0x0400
WM_TRAYICON = WM_USER + 20
WM_NULL = 0x0000
WM_DESTROY = 0x0002
WM_CLOSE = 0x0010
WM_QUERYENDSESSION = 0x0011
WM_ENDSESSION = 0x0016
WM_COMMAND = 0x0111
WM_LBUTTONUP = 0x0202
WM_LBUTTONDBLCLK = 0x0203
WM_RBUTTONUP = 0x0205
WM_CONTEXTMENU = 0x007B

# Shell_NotifyIcon
NIM_ADD = 0
NIM_MODIFY = 1
NIM_DELETE = 2
NIF_MESSAGE = 0x0001
NIF_ICON = 0x0002
NIF_TIP = 0x0004

ID_OPEN = 101
ID_EXIT = 102

MF_STRING = 0x0000
TPM_LEFTALIGN = 0x0000
TPM_RIGHTBUTTON = 0x0002
TPM_NONOTIFY = 0x0080
TPM_RETURNCMD = 0x0100
IDI_APPLICATION = 32512

_DEBUG = bool(os.environ.get("LOCALAGENT_TRAY_DEBUG"))
_SERIAL = 0
_SERIAL_LOCK = threading.Lock()


def _log_path() -> pathlib.Path:
    """Журнал трея: logs/tray.log, иначе временная папка."""
    try:
        from . import config
        return pathlib.Path(config.LOGS_DIR) / "tray.log"
    except Exception:
        return pathlib.Path(tempfile.gettempdir()) / "localagent-tray.log"


def _log(text: str) -> None:
    """Тихая запись в журнал, ошибки не пробрасываются."""
    line = "%s %s\n" % (time.strftime("%Y-%m-%d %H:%M:%S"), text)
    try:
        path = _log_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(str(path), "a", encoding="utf-8") as fh:
            fh.write(line)
    except Exception:
        pass
    if _DEBUG:
        try:
            sys.stderr.write("[tray] " + text + "\n")
        except Exception:
            pass


class Stub:
    """Заглушка: интерфейс тот же, действий нет."""

    ok = False
    icon_added = False
    thread_name = ""

    def __init__(self, reason: str):
        self.reason = reason or "трей недоступен"
        self.calls = []
        _log("заглушка: " + self.reason)

    def stop(self, timeout: float = 3.0) -> None:
        return None

    def __repr__(self) -> str:
        return "<tray.Stub reason=%r>" % (self.reason,)


def _default_exit() -> None:
    """Выход по умолчанию: гасим движок и завершаем процесс."""
    try:
        from . import engine
        engine.stop()
        _log("движок остановлен")
    except Exception as exc:
        _log("engine.stop() не сработал: %s" % (exc,))
    try:
        sys.stdout.flush()
        sys.stderr.flush()
    except Exception:
        pass
    os._exit(0)


_API = None
_API_LOCK = threading.Lock()


def _api():
    """Загрузить Win32 API и структуры один раз."""
    global _API
    if _API is not None:
        return _API
    with _API_LOCK:
        if _API is not None:
            return _API
        import ctypes.wintypes as wt

        u = ctypes.WinDLL("user32", use_last_error=True)
        sh = ctypes.WinDLL("shell32", use_last_error=True)
        kr = ctypes.WinDLL("kernel32", use_last_error=True)
        gd = ctypes.WinDLL("gdi32", use_last_error=True)

        res = ctypes.c_ssize_t
        wndproc = ctypes.WINFUNCTYPE(res, wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM)

        class GUID(ctypes.Structure):
            _fields_ = [("Data1", wt.DWORD), ("Data2", wt.WORD),
                        ("Data3", wt.WORD), ("Data4", ctypes.c_ubyte * 8)]

        class WNDCLASSW(ctypes.Structure):
            _fields_ = [
                ("style", wt.UINT),
                ("lpfnWndProc", wndproc),
                ("cbClsExtra", ctypes.c_int),
                ("cbWndExtra", ctypes.c_int),
                ("hInstance", wt.HINSTANCE),
                ("hIcon", wt.HICON),
                ("hCursor", wt.HANDLE),
                ("hbrBackground", wt.HBRUSH),
                ("lpszMenuName", wt.LPCWSTR),
                ("lpszClassName", wt.LPCWSTR),
            ]

        class NID(ctypes.Structure):
            _fields_ = [
                ("cbSize", wt.DWORD),
                ("hWnd", wt.HWND),
                ("uID", wt.UINT),
                ("uFlags", wt.UINT),
                ("uCallbackMessage", wt.UINT),
                ("hIcon", wt.HICON),
                ("szTip", wt.WCHAR * 128),
                ("dwState", wt.DWORD),
                ("dwStateMask", wt.DWORD),
                ("szInfo", wt.WCHAR * 256),
                ("uVersion", wt.UINT),
                ("szInfoTitle", wt.WCHAR * 64),
                ("dwInfoFlags", wt.DWORD),
                ("guidItem", GUID),
                ("hBalloonIcon", wt.HICON),
            ]

        class ICONINFO(ctypes.Structure):
            _fields_ = [
                ("fIcon", wt.BOOL),
                ("xHotspot", wt.DWORD),
                ("yHotspot", wt.DWORD),
                ("hbmMask", wt.HBITMAP),
                ("hbmColor", wt.HBITMAP),
            ]

        u.RegisterClassW.restype = wt.ATOM
        u.RegisterClassW.argtypes = [ctypes.POINTER(WNDCLASSW)]
        u.UnregisterClassW.restype = wt.BOOL
        u.UnregisterClassW.argtypes = [wt.LPCWSTR, wt.HINSTANCE]
        u.CreateWindowExW.restype = wt.HWND
        u.CreateWindowExW.argtypes = [
            wt.DWORD, wt.LPCWSTR, wt.LPCWSTR, wt.DWORD,
            ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
            wt.HWND, wt.HMENU, wt.HINSTANCE, wt.LPVOID,
        ]
        u.DefWindowProcW.restype = res
        u.DefWindowProcW.argtypes = [wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM]
        u.DestroyWindow.restype = wt.BOOL
        u.DestroyWindow.argtypes = [wt.HWND]
        u.PostMessageW.restype = wt.BOOL
        u.PostMessageW.argtypes = [wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM]
        u.GetMessageW.restype = ctypes.c_int
        u.GetMessageW.argtypes = [
            ctypes.POINTER(wt.MSG), wt.HWND, wt.UINT, wt.UINT,
        ]
        u.TranslateMessage.restype = wt.BOOL
        u.TranslateMessage.argtypes = [ctypes.POINTER(wt.MSG)]
        u.DispatchMessageW.restype = res
        u.DispatchMessageW.argtypes = [ctypes.POINTER(wt.MSG)]
        u.PostQuitMessage.restype = None
        u.PostQuitMessage.argtypes = [ctypes.c_int]
        u.CreatePopupMenu.restype = wt.HMENU
        u.AppendMenuW.restype = wt.BOOL
        u.AppendMenuW.argtypes = [wt.HMENU, wt.UINT, wt.WPARAM, wt.LPCWSTR]
        u.TrackPopupMenu.restype = ctypes.c_uint
        u.TrackPopupMenu.argtypes = [
            wt.HMENU, wt.UINT, ctypes.c_int, ctypes.c_int,
            ctypes.c_int, wt.HWND, wt.LPVOID,
        ]
        u.DestroyMenu.restype = wt.BOOL
        u.DestroyMenu.argtypes = [wt.HMENU]
        u.GetCursorPos.restype = wt.BOOL
        u.GetCursorPos.argtypes = [ctypes.POINTER(wt.POINT)]
        u.SetForegroundWindow.restype = wt.BOOL
        u.SetForegroundWindow.argtypes = [wt.HWND]
        u.LoadIconW.restype = wt.HICON
        u.LoadIconW.argtypes = [wt.HINSTANCE, ctypes.c_void_p]
        u.CreateIconIndirect.restype = wt.HICON
        u.CreateIconIndirect.argtypes = [ctypes.POINTER(ICONINFO)]
        u.DestroyIcon.restype = wt.BOOL
        u.DestroyIcon.argtypes = [wt.HICON]
        sh.Shell_NotifyIconW.restype = wt.BOOL
        sh.Shell_NotifyIconW.argtypes = [wt.DWORD, ctypes.POINTER(NID)]
        kr.GetModuleHandleW.restype = wt.HINSTANCE
        kr.GetModuleHandleW.argtypes = [wt.LPCWSTR]
        gd.CreateBitmap.restype = wt.HBITMAP
        gd.CreateBitmap.argtypes = [
            ctypes.c_int, ctypes.c_int, wt.UINT, wt.UINT, ctypes.c_void_p,
        ]
        gd.DeleteObject.restype = wt.BOOL
        gd.DeleteObject.argtypes = [wt.HGDIOBJ]

        class Api:
            pass

        api = Api()
        api.wt = wt
        api.u = u
        api.sh = sh
        api.kr = kr
        api.gd = gd
        api.wndproc = wndproc
        api.WNDCLASSW = WNDCLASSW
        api.NID = NID
        api.ICONINFO = ICONINFO
        _API = api
        return api
