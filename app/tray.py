"""Иконка в трее Windows: только ctypes и стандартная библиотека.

    from app import tray
    icon = tray.start("http://localhost:7867", on_exit=my_stop)
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
def _rounded(x, y, size, radius):
    """Покрытие скруглённого квадрата в точке, 0..1."""
    cx = min(max(x, radius), size - radius)
    cy = min(max(y, radius), size - radius)
    dist = math.hypot(x - cx, y - cy)
    return max(0.0, min(1.0, radius - dist + 0.5))


def _icon_pixels(size=32):
    """Пиксели иконки: синий квадрат и три белые точки. Формат BGRA."""
    radius = size * 0.30
    dot = size * 0.075
    dots = ((size * 0.32, size * 0.52), (size * 0.5, size * 0.52),
            (size * 0.68, size * 0.52))
    samples = ((0.25, 0.25), (0.75, 0.25), (0.25, 0.75), (0.75, 0.75))
    out = []
    for y in range(size):
        for x in range(size):
            cover = 0.0
            for ox, oy in samples:
                cover += _rounded(x + ox, y + oy, size, radius)
            cover /= 4.0
            if cover <= 0.0:
                out.append((0, 0, 0, 0))
                continue
            t = (x + y) / float(2 * max(size - 1, 1))
            red = int(0x33 + (0x77 - 0x33) * t)
            green = int(0x5C + (0x3D - 0x5C) * t)
            blue = int(0xEF + (0xE6 - 0xEF) * t)
            white = 0.0
            for cx, cy in dots:
                dist = math.hypot(x + 0.5 - cx, y + 0.5 - cy)
                white = max(white, max(0.0, min(1.0, dot - dist + 0.5)))
            if white > 0.0:
                red = int(red + (250 - red) * white)
                green = int(green + (251 - green) * white)
                blue = int(blue + (255 - blue) * white)
            out.append((blue, green, red, int(255 * cover)))
    return out


def _make_icon(size=32):
    """HICON через CreateIconIndirect: цветной CreateBitmap и маска."""
    api = _api()
    pixels = _icon_pixels(size)
    color = (ctypes.c_ubyte * (size * size * 4))()
    for index, (blue, green, red, alpha) in enumerate(pixels):
        color[index * 4] = blue
        color[index * 4 + 1] = green
        color[index * 4 + 2] = red
        color[index * 4 + 3] = alpha
    mask = (ctypes.c_ubyte * (((size + 7) // 8) * size))()
    hbm_c = api.gd.CreateBitmap(size, size, 1, 32,
                                ctypes.cast(color, ctypes.c_void_p))
    hbm_m = api.gd.CreateBitmap(size, size, 1, 1,
                                ctypes.cast(mask, ctypes.c_void_p))
    hicon = 0
    try:
        if not (hbm_c and hbm_m):
            return 0
        info = api.ICONINFO()
        info.fIcon = 1
        info.hbmMask = hbm_m
        info.hbmColor = hbm_c
        hicon = int(api.u.CreateIconIndirect(ctypes.byref(info)) or 0)
    finally:
        if hbm_c:
            api.gd.DeleteObject(hbm_c)
        if hbm_m:
            api.gd.DeleteObject(hbm_m)
    return hicon
class TrayIcon:
    """Иконка в трее. Окно и цикл сообщений живут в потоке tray-icon."""

    def __init__(self, url, on_exit=None):
        global _SERIAL
        self.url = url
        self.on_exit = on_exit
        self.ok = False
        self.reason = ""
        self.icon_added = False
        self.calls = []
        self.thread_name = "tray-icon"
        self.ready = threading.Event()
        self.api = None
        self._hwnd = 0
        self._hicon = 0
        self._own_icon = False
        self._nid = None
        self._stopped = False
        self._lock = threading.RLock()
        self._proc = None
        self._hinst = 0
        with _SERIAL_LOCK:
            _SERIAL += 1
            serial = _SERIAL
        self._cls = "LocalAgentTray_%d_%d" % (os.getpid(), serial)
        self._thread = threading.Thread(target=self._run, name="tray-icon",
                                        daemon=True)

    @property
    def hwnd(self):
        return self._hwnd

    @property
    def alive(self):
        return self._thread.is_alive()

    def start(self):
        """Запустить поток. Возвращает self."""
        self._thread.start()
        return self

    def stop(self, timeout=3.0):
        """Убрать иконку и завершить поток. Повторный вызов безопасен."""
        with self._lock:
            if self._stopped:
                return
            self._stopped = True
        hwnd = self._hwnd
        if hwnd:
            try:
                self.api.u.PostMessageW(hwnd, WM_CLOSE, 0, 0)
            except Exception as exc:
                _log("PostMessage WM_CLOSE: %s" % (exc,))
        current = threading.current_thread()
        if self.alive and current is not self._thread:
            self._thread.join(timeout)
        self._remove_icon()
        if self._thread.is_alive():
            _log("поток трея не остановился за %.1f с" % (timeout,))

    def open_url(self):
        """Открыть адрес в браузере, не блокируя цикл сообщений."""
        threading.Thread(target=self._open, name="tray-open",
                         daemon=True).start()

    def _open(self):
        try:
            webbrowser.open(self.url)
            _log("открыт адрес " + self.url)
        except Exception as exc:
            _log("браузер не открылся: %s" % (exc,))

    def _remove_icon(self):
        nid = self._nid
        with self._lock:
            if nid is None or not self.icon_added:
                return
            self.icon_added = False
        result = 0
        try:
            result = self.api.sh.Shell_NotifyIconW(NIM_DELETE,
                                                   ctypes.byref(nid))
        except Exception as exc:
            _log("NIM_DELETE упал: %s" % (exc,))
        self.calls.append(("NIM_DELETE", bool(result)))
        if not result:
            _log("Shell_NotifyIcon(NIM_DELETE) вернул 0")

    def _run(self):
        hwnd = 0
        try:
            self.api = api = _api()
            api.u.LoadIconW.argtypes = [api.wt.HINSTANCE, ctypes.c_void_p]
            self._hinst = int(api.kr.GetModuleHandleW(None) or 0)
            self._proc = api.wndproc(self._wndproc)
            wc = api.WNDCLASSW()
            wc.style = 0
            wc.lpfnWndProc = self._proc
            wc.hInstance = self._hinst
            wc.lpszClassName = self._cls
            if not api.u.RegisterClassW(ctypes.byref(wc)):
                raise OSError("RegisterClassW: %d" % ctypes.get_last_error())
            hwnd = int(api.u.CreateWindowExW(0, self._cls, TITLE, 0, 0, 0, 0, 0,
                                             None, None, self._hinst, None) or 0)
            if not hwnd:
                raise OSError("CreateWindowExW: %d" % ctypes.get_last_error())
            self._hwnd = hwnd
            self._hicon = _make_icon(32)
            self._own_icon = bool(self._hicon)
            if not self._hicon:
                self._hicon = int(api.u.LoadIconW(None, IDI_APPLICATION) or 0)
            nid = api.NID()
            nid.cbSize = ctypes.sizeof(api.NID)
            nid.hWnd = hwnd
            nid.uID = 1
            nid.uFlags = NIF_MESSAGE | NIF_ICON | NIF_TIP
            nid.uCallbackMessage = WM_TRAYICON
            nid.hIcon = self._hicon
            nid.szTip = ("%s — %s" % (TITLE, self.url))[:127]
            self._nid = nid
            added = bool(api.sh.Shell_NotifyIconW(NIM_ADD, ctypes.byref(nid)))
            self.calls.append(("NIM_ADD", added))
            if not added:
                raise OSError("Shell_NotifyIcon(NIM_ADD) = 0")
            self.icon_added = True
            self.ok = True
            _log("иконка в трее, подсказка: " + str(nid.szTip))
            self.ready.set()
            self._loop()
        except Exception as exc:
            self.reason = self.reason or str(exc) or exc.__class__.__name__
            _log("трей не поднялся: %s" % (self.reason,))
            _log(traceback.format_exc().replace("\n", " | ")[:800])
        finally:
            self._remove_icon()
            if self._own_icon and self._hicon:
                try:
                    self.api.u.DestroyIcon(self._hicon)
                except Exception:
                    pass
                self._hicon = 0
            if hwnd:
                try:
                    self.api.u.DestroyWindow(hwnd)
                except Exception:
                    pass
                self._hwnd = 0
            if self._cls and self._hinst:
                try:
                    self.api.u.UnregisterClassW(self._cls, self._hinst)
                except Exception:
                    pass
            self.ready.set()
            _log("поток трея завершён, ok=%s" % (self.ok,))

    def _loop(self):
        msg = self.api.wt.MSG()
        while True:
            got = self.api.u.GetMessageW(ctypes.byref(msg), None, 0, 0)
            if got <= 0:
                if got < 0:
                    _log("GetMessageW = -1, код %d"
                         % (ctypes.get_last_error(),))
                break
            self.api.u.TranslateMessage(ctypes.byref(msg))
            self.api.u.DispatchMessageW(ctypes.byref(msg))


    def _menu(self):
        """Контекстное меню: Открыть / Выход."""
        api = self.api
        hmenu = api.u.CreatePopupMenu()
        if not hmenu:
            _log("CreatePopupMenu вернул пусто")
            return
        cmd = 0
        try:
            api.u.AppendMenuW(hmenu, MF_STRING, ID_OPEN, "Открыть")
            api.u.AppendMenuW(hmenu, MF_STRING, ID_EXIT, "Выход")
            point = api.wt.POINT()
            api.u.GetCursorPos(ctypes.byref(point))
            api.u.SetForegroundWindow(self._hwnd)
            flags = TPM_LEFTALIGN | TPM_RIGHTBUTTON | TPM_RETURNCMD | TPM_NONOTIFY
            cmd = int(api.u.TrackPopupMenu(hmenu, flags, point.x, point.y,
                                           0, self._hwnd, None))
            api.u.PostMessageW(self._hwnd, WM_NULL, 0, 0)
        except Exception as exc:
            _log("меню не показалось: %s" % (exc,))
        finally:
            api.u.DestroyMenu(hmenu)
        if cmd == ID_OPEN:
            self.open_url()
        elif cmd == ID_EXIT:
            self._exit()

    def _exit(self):
        """Пункт «Выход»: снять иконку, вызвать колбэк, закрыть окно."""
        _log("выбран выход")
        self._remove_icon()
        callback = self.on_exit or _default_exit
        threading.Thread(target=self._call_exit, args=(callback,),
                         name="tray-exit", daemon=True).start()
        if self._hwnd:
            self.api.u.PostMessageW(self._hwnd, WM_CLOSE, 0, 0)

    def _call_exit(self, callback):
        try:
            callback()
            _log("колбэк выхода выполнен")
        except Exception as exc:
            _log("колбэк выхода упал: %s" % (exc,))

    def _wndproc(self, hwnd, msg, wparam, lparam):
        try:
            if msg == WM_TRAYICON:
                event = int(lparam) & 0xFFFF
                if event == WM_LBUTTONDBLCLK:
                    self.open_url()
                elif event in (WM_RBUTTONUP, WM_CONTEXTMENU):
                    self._menu()
                return 0
            if msg == WM_COMMAND:
                cmd = int(wparam) & 0xFFFF
                if cmd == ID_OPEN:
                    self.open_url()
                    return 0
                if cmd == ID_EXIT:
                    self._exit()
                    return 0
            if msg == WM_CLOSE:
                self._remove_icon()
                self.api.u.DestroyWindow(hwnd)
                return 0
            if msg == WM_DESTROY:
                self.api.u.PostQuitMessage(0)
                return 0
            if msg == WM_QUERYENDSESSION:
                return 1
            if msg == WM_ENDSESSION:
                if wparam:
                    self._remove_icon()
                    self.api.u.PostQuitMessage(0)
                return 0
        except Exception as exc:
            _log("ошибка в окне: %s" % (exc,))
            return 0
        return self.api.u.DefWindowProcW(hwnd, msg, wparam, lparam)

def start(url=DEFAULT_URL, on_exit=None):

    """Поднять иконку в трее.

    url     — что открывать по двойному клику и из меню.
    on_exit — что делать при «Выход»; по умолчанию стоп движка и выход.

    Возвращает объект с stop(). Если трей не поддержан — Stub.
    """
    url = (url or "").strip() or DEFAULT_URL
    if not IS_WINDOWS:
        return Stub("трей работает только в Windows")
    try:
        _api()
    except Exception as exc:
        return Stub("Win32 недоступен: %s" % (exc,))

    icon = TrayIcon(url, on_exit)
    try:
        icon.start()
    except Exception as exc:
        return Stub("поток трея не стартовал: %s" % (exc,))
    if not icon.ready.wait(6.0):
        icon.stop()
        return Stub("окно трея не создалось за 6 с")
    if not icon.ok:
        reason = icon.reason or "иконка не добавлена"
        icon.stop()
        return Stub(reason)
    return icon


def main():
    """Ручной запуск: python -m app.tray [url]."""
    url = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_URL
    icon = start(url)
    print("трей:", "поднят" if icon.ok else ("не поднят (%s)" % icon.reason))
    if not icon.ok:
        return 1
    try:
        while icon.alive:
            time.sleep(0.5)
    except KeyboardInterrupt:
        icon.stop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
