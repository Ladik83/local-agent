"""Иконка в системном трее Windows: только ctypes и стандартная библиотека.

Использование:
    from app import tray
    icon = tray.start("http://127.0.0.1:7867", on_exit=my_shutdown)
    ...
    icon.stop()

Всё живёт в отдельном потоке: скрытое окно-приёмник сообщений плюс цикл
GetMessage. Вызывающий код не блокируется. stop() отправляет WM_CLOSE,
окно удаляется (WM_DESTROY), иконка убирается через Shell_NotifyIcon.
"""
from __future__ import annotations

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
DEFAULT_URL = "http://127.0.0.1:7867"

# окна и сообщения
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
NIM_ADD, NIM_MODIFY, NIM_DELETE = 0, 1, 2
NIF_MESSAGE, NIF_ICON, NIF_TIP = 0x0001, 0x0002, 0x0004
ID_OPEN, ID_EXIT = 101, 102
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
    """Журнал трея: app-логи, иначе временная папка."""
    try:
        from . import config  # noqa: WPS433 - локальный импорт, чтобы не тянуть всё
        return pathlib.Path(config.LOGS_DIR) / "tray.log"
    except Exception:
        return pathlib.Path(tempfile.gettempdir()) / "localagent-tray.log"


def _log(text: str) -> None:
    """Тихая запись в журнал. Ошибки логирования не пробрасываются."""
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
    """Заглушка: тот же интерфейс, но ничего не делает."""

    ok = False
    icon_added = False
    thread_name = ""
    calls: list = []

    def __init__(self, reason: str):
        self.reason = reason or "трей недоступен"
        _log("заглушка: " + self.reason)

    def stop(self, timeout: float = 3.0) -> None:
        return None

    def __repr__(self) -> str:
        return "<tray.Stub reason=%r>" % (self.reason,)


def start(url: str = DEFAULT_URL, on_exit=None):
    """Поднять иконку в трее.

    url     — адрес, который открывается по двойному клику и из меню.
    on_exit — что сделать при «Выход». По умолчанию: остановить движок
              и завершить процесс.

    Возвращает объект с методом stop(); при невозможности — Stub.
    """
    url = (url or "").strip() or DEFAULT_URL
    if not IS_WINDOWS:
        return Stub("трей реализован только для Windows")
    if not _WIN_OK:
        return Stub("не удалось загрузить user32/shell32/gdi32")

    icon = TrayIcon(url, on_exit)
    try:
        icon.start()
    except Exception as exc:
        _log("не удалось запустить поток трея: %s" % (exc,))
        return Stub("поток трея не запустился: %s" % (exc,))

    if not icon.ready.wait(6.0):
        icon.stop()
        return Stub("окно трея не создалось за 6 секунд")

    if not icon.ok:
        reason = icon.reason or "иконка не добавлена"
        icon.stop()
        return Stub(reason)

    return icon


def _default_exit() -> None:
    """Выход по умолчанию: остановить движок и завершить процесс."""
    try:
        from . import engine
        engine.stop()
        _log("движок остановлен перед выходом")
    except Exception as exc:
        _log("engine.stop() не сработал: %s" % (exc,))
    try:
        sys.stdout.flush()
        sys.stderr.flush()
    except Exception:
        pass
    os._exit(0)


if IS_WINDOWS:
    import ctypes
    import ctypes.wintypes as wt

    try:
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        shell32 = ctypes.WinDLL("shell32", use_last_error=True)
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)
        _WIN_OK = True
    except Exception as _exc:  # библиотеки недоступны — работаем заглушкой
        _WIN_OK = False
        _log("WinDLL не загрузился: %s" % (_exc,))

if IS_WINDOWS and _WIN_OK:
    LRESULT = ctypes.c_ssize_t
    WNDPROC = ctypes.WINFUNCTYPE(LRESULT, wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM)

    class GUID(ctypes.Structure):
        _fields_ = [("Data1", wt.DWORD), ("Data2", wt.WORD), ("Data3", wt.WORD),
                    ("Data4", ctypes.c_ubyte * 8)]

    class WNDCLASSW(ctypes.Structure):
        _fields_ = [("style", wt.UINT), ("lpfnWndProc", WNDPROC),
                    ("cbClsExtra", ctypes.c_int), ("cbWndExtra", ctypes.c_int),
                    ("hInstance", wt.HINSTANCE), ("hIcon", wt.HICON),
                    ("hCursor", wt.HANDLE), ("hbrBackground", wt.HBRUSH),
                    ("lpszMenuName", wt.LPCWSTR), ("lpszClassName", wt.LPCWSTR)]

    class NOTIFYICONDATAW(ctypes.Structure):
        _fields_ = [("cbSize", wt.DWORD), ("hWnd", wt.HWND), ("uID", wt.UINT),
                    ("uFlags", wt.UINT), ("uCallbackMessage", wt.UINT),
                    ("hIcon", wt.HICON), ("szTip", wt.WCHAR * 128),
                    ("dwState", wt.DWORD), ("dwStateMask", wt.DWORD),
                    ("szInfo", wt.WCHAR * 256), ("uVersion", wt.UINT),
                    ("szInfoTitle", wt.WCHAR * 64), ("dwInfoFlags", wt.DWORD),
                    ("guidItem", GUID), ("hBalloonIcon", wt.HICON)]

    class ICONINFO(ctypes.Structure):
        _fields_ = [("fIcon", wt.BOOL), ("xHotspot", wt.DWORD), ("yHotspot", wt.DWORD),
                    ("hbmMask", wt.HBITMAP), ("hbmColor", wt.HBITMAP)]

    user32.RegisterClassW.argtypes = [ctypes.POINTER(WNDCLASSW)]
    user32.RegisterClassW.restype = wt.ATOM
    user32.UnregisterClassW.argtypes = [wt.LPCWSTR, wt.HINSTANCE]
    user32.UnregisterClassW.restype = wt.BOOL
    user32.CreateWindowExW.restype = wt.HWND
    user32.CreateWindowExW.argtypes = [wt.DWORD, wt.LPCWSTR, wt.LPCWSTR, wt.DWORD,
                                       ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                       ctypes.c_int, wt.HWND, wt.HMENU, wt.HINSTANCE,
                                       wt.LPVOID]
    user32.DefWindowProcW.argtypes = [wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM]
    user32.DefWindowProcW.restype = LRESULT
    user32.DestroyWindow.argtypes = [wt.HWND]
    user32.DestroyWindow.restype = wt.BOOL
    user32.PostMessageW.argtypes = [wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM]
    user32.PostMessageW.restype = wt.BOOL
    user32.GetMessageW.argtypes = [ctypes.POINTER(wt.MSG), wt.HWND, wt.UINT, wt.UINT]
    user32.GetMessageW.restype = ctypes.c_int
    user32.TranslateMessage.argtypes = [ctypes.POINTER(wt.MSG)]
    user32.DispatchMessageW.argtypes = [ctypes.POINTER(wt.MSG)]
    user32.DispatchMessageW.restype = LRESULT
    user32.PostQuitMessage.argtypes = [ctypes.c_int]
    user32.CreatePopupMenu.restype = wt.HMENU
    user32.AppendMenuW.argtypes = [wt.HMENU, wt.UINT, wt.WPARAM, wt.LPCWSTR]
    user32.AppendMenuW.restype = wt.BOOL
    user32.TrackPopupMenu.argtypes = [wt.HMENU, wt.UINT, ctypes.c_int, ctypes.c_int,
                                      ctypes.c_int, wt.HWND, wt.LPVOID]
    user32.TrackPopupMenu.restype = wt.UINT
    user32.DestroyMenu.argtypes = [wt.HMENU]
    user32.GetCursorPos.argtypes = [ctypes.POINTER(wt.POINT)]
    user32.GetCursorPos.restype = wt.BOOL
    user32.SetForegroundWindow.argtypes = [wt.HWND]
    user32.LoadIconW.argtypes = [wt.HINSTANCE, wt.LPCWSTR]
    user32.LoadIconW.restype = wt.HICON
    user32.CreateIconIndirect.argtypes = [ctypes.POINTER(ICONINFO)]
    user32.CreateIconIndirect.restype = wt.HICON
    user32.DestroyIcon.argtypes = [wt.HICON]
    shell32.Shell_NotifyIconW.argtypes = [wt.DWORD, ctypes.POINTER(NOTIFYICONDATAW)]
    shell32.Shell_NotifyIconW.restype = wt.BOOL
    kernel32.GetModuleHandleW.argtypes = [wt.LPCWSTR]
    kernel32.GetModuleHandleW.restype = wt.HINSTANCE
    gdi32.CreateBitmap.restype = wt.HBITMAP
    gdi32.CreateBitmap.argtypes = [ctypes.c_int, ctypes.c_int, wt.UINT, wt.UINT,
                                   ctypes.c_void_p]
    gdi32.DeleteObject.argtypes = [wt.HGDIOBJ]
    gdi32.DeleteObject.restype = wt.BOOL


def _rounded(x: float, y: float, size: int, radius: float) -> float:
    """Покрытие скруглённого квадрата в точке (0..1)."""
    cx = min(max(x, radius), size - radius)
    cy = min(max(y, radius), size - radius)
    dist = math.hypot(x - cx, y - cy)
    return max(0.0, min(1.0, radius - dist + 0.5))


def _icon_pixels(size: int = 32) -> list:
    """Скруглённый квадрат с градиентом и тремя точками. Список (b, g, r, a)."""
    radius = size * 0.30
    dot_r = size * 0.070
    dots = ((size * 0.32, size * 0.52), (size * 0.50, size * 0.52), (size * 0.68, size * 0.52))
    out = []
    for y in range(size):
        for x in range(size):
            body = 0.0
            for ox, oy in ((0.25, 0.25), (0.75, 0.25), (0.25, 0.75), (0.75, 0.75)):
                body += _rounded(x + ox, y + oy, size, radius)
            body /= 4.0
            if body <= 0.0:
                out.append((0, 0, 0, 0))
                continue
            t = (x + y) / float(2 * max(size - 1, 1))
            red = int(0x33 + (0x77 - 0x33) * t)
            green = int(0x5c + (0x3d - 0x5c) * t)
            blue = int(0xef + (0xe6 - 0xef) * t)
            white = 0.0
            for cx, cy in dots:
                d = math.hypot(x + 0.5 - cx, y + 0.5 - cy)
                white = max(white, max(0.0, min(1.0, dot_r - d + 0.5)))
            if white > 0.0:
                red = int(red + (250 - red) * white)
                green = int(green + (251 - green) * white)
                blue = int(blue + (255 - blue) * white)
            out.append((blue, green, red, int(255 * body)))
    return out


def _make_icon(size: int = 32) -> int:
    """HICON через CreateIconIndirect (CreateBitmap + маска). 0 — не вышло."""
    if not (IS_WINDOWS and _WIN_OK):
        return 0
    pixels = _icon_pixels(size)
    color = (ctypes.c_ubyte * (size * size * 4))()
    for i, (b, g, r, a) in enumerate(pixels):
        color[i * 4 + 0] = b
        color[i * 4 + 1] = g
        color[i * 4 + 2] = r
        color[i * 4 + 3] = a
    mask = (ctypes.c_ubyte * ((size + 7) // 8 * size))()  # маска: всё непрозрачное
    hbm_color = gdi32.CreateBitmap(size, size, 1, 32, ctypes.cast(color, ctypes.c_void_p))
    hbm_mask = gdi32.CreateBitmap(size, size, 1, 1, ctypes.cast(mask, ctypes.c_void_p))
    hicon = 0
    try:
        if hbm_color and hbm_mask:
            info = ICONINFO()
            info.fIcon = 1
            info.xHotspot = 0
            info.yHotspot = 0
            info.hbmMask = hbm_mask
            info.hbmColor = hbm_color
            hicon = int(user32.CreateIconIndirect(ctypes.byref(info)) or 0)
    finally:
        if hbm_color:
            gdi32.DeleteObject(hbm_color)
        if hbm_mask:
            gdi32.DeleteObject(hbm_mask)
    return hicon


class TrayIcon:
    """Иконка в трее; вся работа — в потоке ``tray-icon``."""

    def __init__(self, url: str, on_exit=None):
        global _SERIAL
        self.url = url
        self.on_exit = on_exit
        self.ok = False
        self.reason = ""
        self.icon_added = False
        self.calls = []          # [(операция NIM_*, результат)]
        self.thread_name = "tray-icon"
        self.ready = threading.Event()
        self._hwnd = 0
        self._hicon = 0
        self._own_icon = False
        self._nid = None
        self._stopped = False
        self._lock = threading.RLock()
        self._wndproc_ref = None
        self._hinst = 0
        self._class = ""
        with _SERIAL_LOCK:
            _SERIAL += 1
            serial = _SERIAL
        self._class = "LocalAgentTray_%d_%d" % (os.getpid(), serial)
        self._thread = threading.Thread(target=self._run, name="tray-icon", daemon=True)

    # --- публичная часть -------------------------------------------------
    @property
    def hwnd(self) -> int:
        return self._hwnd

    @property
    def alive(self) -> bool:
        return self._thread.is_alive()

    def start(self) -> None:
        self._thread.start()

    def stop(self, timeout: float = 3.0) -> None:
        """Убрать иконку и завершить поток. Безопасно вызывать повторно."""
        with self._lock:
            if self._stopped:
                return
            self._stopped = True
        hwnd = self._hwnd
        if hwnd:
            try:
                user32.PostMessageW(hwnd, WM_CLOSE, 0, 0)
            except Exception as exc:
                _log("PostMessage(WM_CLOSE) не прошёл: %s" % (exc,))
        if self._thread.is_alive() and threading.current_thread() is not self._thread:
            self._thread.join(timeout)
        if self._thread.is_alive():
            _log("поток трея не завершился за %.1f с" % (timeout,))
        else:
            self._remove_icon()

    def open_url(self) -> None:
        threading.Thread(target=self._open, name="tray-open", daemon=True).start()

    # --- внутреннее ------------------------------------------------------
    def _open(self) -> None:
        try:
            webbrowser.open(self.url)
            _log("открыт адрес " + self.url)
        except Exception as exc:
            _log("не удалось открыть браузер: %s" % (exc,))

    def _remove_icon(self) -> None:
        with self._lock:
            nid = self._nid
            if nid is None or not self.icon_added:
                return
            try:
                res = shell32.Shell_NotifyIconW(NIM_DELETE, ctypes.byref(nid))
                self.calls.append(("NIM_DELETE", bool(res)))
                self.icon_added = False
                if not res:
                    _log("Shell_NotifyIcon(NIM_DELETE) вернул 0")
            except Exception as exc:
                _log("NIM_DELETE упал: %s" % (exc,))
        if self._own_icon and self._hicon:
            try:
                user32.DestroyIcon(self._hicon)
            except Exception:
                pass
            self._own_icon = False
            self._hicon = 0

    def _run(self) -> None:
        try:
            self._hinst = int(kernel32.GetModuleHandleW(None) or 0)
            self._wndproc_ref = WNDPROC(self._wndproc)
            wc = WNDCLASSW()
            wc.style = 0
            wc.lpfnWndProc = self._wndproc_ref
            wc.cbClsExtra = 0
            wc.cbWndExtra = 0
            wc.hInstance = self._hinst
            wc.hIcon = 0
            wc.hCursor = 0
            wc.hbrBackground = 0
            wc.lpszMenuName = None
            wc.lpszClassName = self._class
            atom = user32.RegisterClassW(ctypes.byref(wc))
            if not atom:
                raise OSError("RegisterClassW: код %d" % ctypes.get_last_error())

            self._hwnd = int(user32.CreateWindowExW(0, self._class, TITLE, 0, 0, 0, 0, 0,
                                                    None, None, self._hinst, None) or 0)
            if not self._hwnd:
                raise OSError("CreateWindowExW: код %d" % ctypes.get_last_error())

            self._hicon = _make_icon(32)
            if self._hicon:
                self._own_icon = True
            else:
                self._hicon = int(user32.LoadIconW(None, wt.LPCWSTR(IDI_APPLICATION)) or 0)
                _log("своя иконка не создалась, взята системная")

            nid = NOTIFYICONDATAW()
            nid.cbSize = ctypes.sizeof(NOTIFYICONDATAW)
            nid.hWnd = self._hwnd
            nid.uID = 1
            nid.uFlags = NIF_MESSAGE | NIF_ICON | NIF_TIP
            nid.uCallbackMessage = WM_TRAYICON
            nid.hIcon = self._hicon
            nid.szTip = ("%s — %s" % (TITLE, self.url))[:127]
            self._nid = nid
            added = bool(shell32.Shell_NotifyIconW(NIM_ADD, ctypes.byref(nid)))
            self.calls.append(("NIM_ADD", added))
            if not added:
                self.reason = "Shell_NotifyIcon(NIM_ADD) вернул 0"
                _log(self.reason)
                return
            self.icon_added = True
            self.ok = True
            _log("иконка добавлена, подсказка: " + str(nid.szTip))
            self.ready.set()
            self._loop()
        except Exception as exc:
            self.reason = str(exc) or exc.__class__.__name__
            _log("поток трея упал: %s\n%s" % (self.reason, traceback.format_exc()))
        finally:
            self._remove_icon()
            if self._hwnd:
                try:
                    user32.DestroyWindow(self._hwnd)
                except Exception:
                    pass
                self._hwnd = 0
            if self._class:
                try:
                    user32.UnregisterClassW(self._class, self._hinst)
                except Exception:
                    pass
            self.ready.set()
            _log("поток трея завершён")

    def _loop(self) -> None:
        msg = wt.MSG()
        while True:
            got = user32.GetMessageW(ctypes.byref(msg), None, 0, 0)
            if got == 0:
                break
            if got == -1:
                _log("GetMessageW вернул -1: %s" % ctypes.get_last_error())
                break
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))

    def _menu(self) -> None:
        """Контекстное меню: Открыть / Выход."""
        hmenu = user32.CreatePopupMenu()
        if not hmenu:
            _log("CreatePopupMenu не удался")
            return
        try:
            user32.AppendMenuW(hmenu, MF_STRING, ID_OPEN, "Открыть")
            user32.AppendMenuW(hmenu, MF_STRING, ID_EXIT, "Выход")
            point = wt.POINT()
            user32.GetCursorPos(ctypes.byref(point))
            user32.SetForegroundWindow(self._hwnd)
            cmd = int(user32.TrackPopupMenu(hmenu, TPM_LEFTALIGN | TPM_RIGHTBUTTON
                                            | TPM_RETURNCMD | TPM_NONOTIFY,
                                            point.x, point.y, 0, self._hwnd, None))
            user32.PostMessageW(self._hwnd, WM_NULL, 0, 0)
        finally:
            user32.DestroyMenu(hmenu)
        if cmd == ID_OPEN:
            self.open_url()
        elif cmd == ID_EXIT:
            self._exit()

    def _exit(self) -> None:
        """Пункт «Выход»: убрать иконку, вызвать колбэк, закрыть окно."""
        _log("выбран выход")
        self._remove_icon()
        callback = self.on_exit or _default_exit
        threading.Thread(target=self._call_exit, args=(callback,),
                         name="tray-exit", daemon=True).start()
        if self._hwnd:
            user32.PostMessageW(self._hwnd, WM_CLOSE, 0, 0)

    def _call_exit(self, callback) -> None:
        try:
            callback()
            _log("колбэк выхода выполнен")
        except Exception as exc:
            _log("колбэк выхода упал: %s\n%s" % (exc, traceback.format_exc()))

    def _wndproc(self, hwnd, msg, wparam, lparam):
        try:
            if msg == WM_TRAYICON:
                event = int(lparam) & 0xFFFF
                if event in (WM_LBUTTONDBLCLK,):
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
                user32.DestroyWindow(hwnd)
                return 0
            if msg == WM_DESTROY:
                user32.PostQuitMessage(0)
                return 0
            if msg == WM_QUERYENDSESSION:
                return 1
            if msg == WM_ENDSESSION:
                if wparam:
                    self._remove_icon()
                    user32.PostQuitMessage(0)
                return 0
        except Exception as exc:
            _log("ошибка в окне трея: %s\n%s" % (exc, traceback.format_exc()))
            return 0
        return user32.DefWindowProcW(hwnd, msg, wparam, lparam)
else:
    _WIN_OK = False


def main() -> None:
    """Ручной запуск: python -m app.tray [url]."""
    url = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_URL
    icon = start(url)
    print("трей:", "поднят" if icon.ok else "не поднят (%s)" % icon.reason)
    if not icon.ok:
        return
    try:
        while icon.alive:
            time.sleep(0.5)
    except KeyboardInterrupt:
        icon.stop()


if __name__ == "__main__":
    main()
