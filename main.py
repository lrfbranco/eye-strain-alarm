import os
import sys
import time
import ctypes
import winsound
import subprocess

from PySide6.QtCore import QTimer
from PySide6.QtGui import QAction, QIcon, QPixmap, QColor
from PySide6.QtWidgets import QApplication, QSystemTrayIcon, QMenu

# ===============================
# CONFIG
# ===============================
INACTIVITY_LIMIT = 10 * 60       # seconds
REMINDER_INTERVAL = 60 * 60      # seconds
POLL_INTERVAL_MS = 2000          # 2s

BEEP_FREQ = 800
BEEP_DUR = 200
VOICE_TEXT = "Blink"

# ===============================
# WINDOWS: idle time
# ===============================
class LASTINPUTINFO(ctypes.Structure):
    _fields_ = [("cbSize", ctypes.c_uint),
                ("dwTime", ctypes.c_uint)]

def get_idle_time_seconds() -> float:
    lii = LASTINPUTINFO()
    lii.cbSize = ctypes.sizeof(LASTINPUTINFO)
    ctypes.windll.user32.GetLastInputInfo(ctypes.byref(lii))
    millis = ctypes.windll.kernel32.GetTickCount() - lii.dwTime
    return millis / 1000.0

# ===============================
# WINDOWS: fullscreen detection
# ===============================
user32 = ctypes.windll.user32

class RECT(ctypes.Structure):
    _fields_ = [("left", ctypes.c_long),
                ("top", ctypes.c_long),
                ("right", ctypes.c_long),
                ("bottom", ctypes.c_long)]

class MONITORINFO(ctypes.Structure):
    _fields_ = [("cbSize", ctypes.c_ulong),
                ("rcMonitor", RECT),
                ("rcWork", RECT),
                ("dwFlags", ctypes.c_ulong)]

def _rect_area(r: RECT) -> int:
    w = max(0, r.right - r.left)
    h = max(0, r.bottom - r.top)
    return w * h

def is_foreground_fullscreen() -> bool:
    hwnd = user32.GetForegroundWindow()
    if not hwnd:
        return False

    wr = RECT()
    if not user32.GetWindowRect(hwnd, ctypes.byref(wr)):
        return False

    MONITOR_DEFAULTTONEAREST = 2
    hmon = user32.MonitorFromWindow(hwnd, MONITOR_DEFAULTTONEAREST)
    if not hmon:
        return False

    mi = MONITORINFO()
    mi.cbSize = ctypes.sizeof(MONITORINFO)
    if not user32.GetMonitorInfoW(hmon, ctypes.byref(mi)):
        return False

    tol = 2
    full = (
        abs(wr.left - mi.rcMonitor.left) <= tol and
        abs(wr.top - mi.rcMonitor.top) <= tol and
        abs(wr.right - mi.rcMonitor.right) <= tol and
        abs(wr.bottom - mi.rcMonitor.bottom) <= tol
    )

    if not full:
        win_area = _rect_area(wr)
        mon_area = _rect_area(mi.rcMonitor)
        if mon_area > 0 and win_area / mon_area >= 0.98:
            full = True

    return full

# ===============================
# WINDOWS: reliable TTS via PowerShell (SAPI)
# ===============================
def speak_windows_tts(text: str, repeat: int = 2, pause_ms: int = 150):
    # Uses Windows built-in System.Speech (SAPI)
    safe = text.replace("'", "''")

    # PowerShell loop with sleep between repetitions
    cmd = [
        "powershell",
        "-NoProfile",
        "-ExecutionPolicy", "Bypass",
        "-Command",
        (
            "Add-Type -AssemblyName System.Speech; "
            "$speak = New-Object System.Speech.Synthesis.SpeechSynthesizer; "
            f"for ($i = 0; $i -lt {repeat}; $i++) {{ "
            f"$speak.Speak('{safe}'); "
            f"Start-Sleep -Milliseconds {pause_ms}; "
            "}"
        )
    ]

    subprocess.Popen(
        cmd,
        creationflags=subprocess.CREATE_NO_WINDOW
    )


# ===============================
# Tray app
# ===============================
class ScreenBreakTray:
    def __init__(self):
        self.base_dir = os.path.dirname(os.path.abspath(__file__))

        # State
        self.active = False
        self.last_reminder_time = time.time()
        self.muted = False
        self.mode = "voice"  # "beep" or "voice"
        self.disable_fullscreen = True

        # Icons
        self.icon_active = self._load_icon("active.ico", fallback_color=QColor(0, 180, 0))
        self.icon_inactive = self._load_icon("inactive.ico", fallback_color=QColor(180, 180, 180))

        # Tray
        self.tray = QSystemTrayIcon()
        self.tray.setIcon(self.icon_inactive)
        self.tray.setToolTip("ScreenBreak: Inactive")

        self.menu = QMenu()

        self.action_voice = QAction("Use Voice (TTS)")
        self.action_voice.setCheckable(True)
        self.action_voice.triggered.connect(lambda: self._set_mode("voice"))

        self.action_beep = QAction("Use Beep")
        self.action_beep.setCheckable(True)
        self.action_beep.triggered.connect(lambda: self._set_mode("beep"))

        # Radio behavior
        self.action_voice.setChecked(True)
        self.action_beep.setChecked(False)

        self.action_mute = QAction("Muted")
        self.action_mute.setCheckable(True)
        self.action_mute.triggered.connect(self._toggle_mute)

        self.action_fullscreen = QAction("Disable reminders during fullscreen apps")
        self.action_fullscreen.setCheckable(True)
        self.action_fullscreen.setChecked(True)
        self.action_fullscreen.triggered.connect(self._toggle_fullscreen)

        self.action_quit = QAction("Quit")
        self.action_quit.triggered.connect(self._quit)

        self.menu.addAction(self.action_voice)
        self.menu.addAction(self.action_beep)
        self.menu.addSeparator()
        self.menu.addAction(self.action_mute)
        self.menu.addAction(self.action_fullscreen)
        self.menu.addSeparator()
        self.menu.addAction(self.action_quit)

        self.tray.setContextMenu(self.menu)
        self.tray.show()

        # Timer tick in UI thread
        self.timer = QTimer()
        self.timer.setInterval(POLL_INTERVAL_MS)
        self.timer.timeout.connect(self._tick)
        self.timer.start()

    def _load_icon(self, filename: str, fallback_color: QColor) -> QIcon:
        path = os.path.join(self.base_dir, filename)
        if os.path.exists(path):
            ico = QIcon(path)
            if not ico.isNull():
                return ico

        pm = QPixmap(64, 64)
        pm.fill(fallback_color)
        return QIcon(pm)

    def _set_mode(self, mode: str):
        self.mode = mode
        self.action_voice.setChecked(mode == "voice")
        self.action_beep.setChecked(mode == "beep")

    def _toggle_mute(self):
        self.muted = self.action_mute.isChecked()

    def _toggle_fullscreen(self):
        self.disable_fullscreen = self.action_fullscreen.isChecked()

    def _set_active(self):
        if not self.active:
            self.active = True
            self.last_reminder_time = time.time()
            self.tray.setIcon(self.icon_active)
            self.tray.setToolTip("ScreenBreak: Active")

    def _set_inactive(self):
        if self.active:
            self.active = False
            self.tray.setIcon(self.icon_inactive)
            self.tray.setToolTip("ScreenBreak: Inactive")
        self.last_reminder_time = time.time()

    def _do_reminder(self):
        if self.muted:
            return

        if self.mode == "beep":
            winsound.Beep(BEEP_FREQ, BEEP_DUR)
        else:
            speak_windows_tts(VOICE_TEXT)

    def _tick(self):
        idle = get_idle_time_seconds()

        if idle > INACTIVITY_LIMIT:
            self._set_inactive()
            return

        self._set_active()

        if self.disable_fullscreen and is_foreground_fullscreen():
            # Don't remind while fullscreen; keep pushing timer forward
            self.last_reminder_time = time.time()
            return

        now = time.time()
        if (now - self.last_reminder_time) >= REMINDER_INTERVAL:
            self._do_reminder()
            self.last_reminder_time = now

    def _quit(self):
        self.timer.stop()
        self.tray.hide()
        QApplication.quit()

def main():
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)

    _ = ScreenBreakTray()
    sys.exit(app.exec())

if __name__ == "__main__":
    main()
