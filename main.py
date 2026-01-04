import os
import sys
import time
import ctypes
import winsound
import subprocess

from PySide6.QtCore import QTimer
from PySide6.QtGui import QAction, QIcon, QPixmap, QColor, QActionGroup
from PySide6.QtWidgets import QApplication, QSystemTrayIcon, QMenu

# ===============================
# DEFAULTS (can be changed from tray menu)
# ===============================
DEFAULT_REMINDER_INTERVAL_MIN = 60
DEFAULT_INACTIVITY_LIMIT_MIN = 10

POLL_INTERVAL_MS = 2000  # 2s

BEEP_FREQ = 800
BEEP_DUR = 200
VOICE_TEXT = "Blink"

# Presets shown in tray menu (minutes)
REMINDER_PRESETS_MIN = [.10, .25, 30, 60, 90, 120]
INACTIVITY_PRESETS_MIN = [5, 10, 15, 20, 30]

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
# For beeping.
# ===============================
def beep_chirp(
    freqs=(600, 800, 1000),
    dur_ms=90,
    gap_ms=20
):
    for f in freqs:
        winsound.Beep(int(f), int(dur_ms))
        time.sleep(gap_ms / 1000.0)

# ===============================
# Tray app
# ===============================
class ScreenBreakTray:
    def __init__(self):
        # PyInstaller-safe base directory for bundled assets
        self.base_dir = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))

        # State
        self.active = False
        self.last_reminder_time = time.time()
        self.muted = False
        self.mode = "voice"  # "beep" or "voice"
        self.disable_fullscreen = True

        # User-adjustable timers (seconds)
        self.reminder_interval_s = DEFAULT_REMINDER_INTERVAL_MIN * 60
        self.inactivity_limit_s = DEFAULT_INACTIVITY_LIMIT_MIN * 60

        # Icons
        self.icon_active = self._load_icon("active.ico", fallback_color=QColor(0, 180, 0))
        self.icon_inactive = self._load_icon("inactive.ico", fallback_color=QColor(180, 180, 180))

        # Tray
        self.tray = QSystemTrayIcon()
        self.tray.setIcon(self.icon_inactive)
        self.tray.setToolTip(self._tooltip_text("Inactive"))

        self.menu = QMenu()

        # --- Mode (radio) ---
        self.action_voice = QAction("Use Voice (TTS)")
        self.action_voice.setCheckable(True)
        self.action_voice.triggered.connect(lambda: self._set_mode("voice"))

        self.action_beep = QAction("Use Beep")
        self.action_beep.setCheckable(True)
        self.action_beep.triggered.connect(lambda: self._set_mode("beep"))

        self.action_voice.setChecked(True)
        self.action_beep.setChecked(False)

        # --- Mute / fullscreen ---
        self.action_mute = QAction("Muted")
        self.action_mute.setCheckable(True)
        self.action_mute.triggered.connect(self._toggle_mute)

        self.action_fullscreen = QAction("Disable reminders during fullscreen apps")
        self.action_fullscreen.setCheckable(True)
        self.action_fullscreen.setChecked(True)
        self.action_fullscreen.triggered.connect(self._toggle_fullscreen)

        # --- Reminder Interval submenu (radio) ---
        reminder_menu = QMenu("Reminder interval", self.menu)
        self.reminder_group = QActionGroup(self.menu)
        self.reminder_group.setExclusive(True)
        self.reminder_actions = {}

        for m in REMINDER_PRESETS_MIN:
            act = QAction(f"{m} min", self.menu, checkable=True)
            act.triggered.connect(lambda _=False, mm=m: self._set_reminder_interval_minutes(mm))
            self.reminder_group.addAction(act)
            reminder_menu.addAction(act)
            self.reminder_actions[m] = act

        # Set default checked
        self.reminder_actions.get(DEFAULT_REMINDER_INTERVAL_MIN, self.reminder_actions[REMINDER_PRESETS_MIN[0]]).setChecked(True)

        # --- Inactivity Limit submenu (radio) ---
        inactivity_menu = QMenu("Inactivity limit", self.menu)
        self.inactivity_group = QActionGroup(self.menu)
        self.inactivity_group.setExclusive(True)
        self.inactivity_actions = {}

        for m in INACTIVITY_PRESETS_MIN:
            act = QAction(f"{m} min", self.menu, checkable=True)
            act.triggered.connect(lambda _=False, mm=m: self._set_inactivity_limit_minutes(mm))
            self.inactivity_group.addAction(act)
            inactivity_menu.addAction(act)
            self.inactivity_actions[m] = act

        # Set default checked
        self.inactivity_actions.get(DEFAULT_INACTIVITY_LIMIT_MIN, self.inactivity_actions[INACTIVITY_PRESETS_MIN[0]]).setChecked(True)

        # Quit
        self.action_quit = QAction("Quit")
        self.action_quit.triggered.connect(self._quit)

        # Build menu
        self.menu.addAction(self.action_voice)
        self.menu.addAction(self.action_beep)
        self.menu.addSeparator()
        self.menu.addAction(self.action_mute)
        self.menu.addAction(self.action_fullscreen)
        self.menu.addSeparator()
        self.menu.addMenu(reminder_menu)
        self.menu.addMenu(inactivity_menu)
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
    
    def _fmt_time(self, seconds: float) -> str:
        if seconds < 60:
            return f"{int(seconds)}s"
        else:
            mins = seconds / 60
            return f"{mins:g}m"
            
    def _fmt_mmss(self, seconds: float) -> str:
        seconds = max(0.0, float(seconds))
        if seconds < 60:
            return f"{int(round(seconds))}s"
        mins = int(seconds // 60)
        secs = int(round(seconds - 60 * mins))
        if secs == 60:
            mins += 1
            secs = 0
        return f"{mins}m {secs:02d}s"

    def _fmt_mins_only(self, seconds: float) -> str:
        # for the "X mins" wording; show fractional minutes if < 2 minutes
        seconds = max(0.0, float(seconds))
        mins = seconds / 60.0
        if mins < 2:
            return f"{mins:.1f}"
        return f"{int(round(mins))}"

    def _tooltip_text(self, state: str) -> str:
        mode_txt = "Voice" if self.mode == "voice" else "Beep"
        mute_txt = "Muted" if self.muted else "Sound on"
        fs_txt = "NoFS" if self.disable_fullscreen else "FSok"

        if not self.active:
            idle = get_idle_time_seconds()
            return (
                f"ScreenBreak: Inactive\n"
                f"Last input: {self._fmt_mmss(idle)} ago\n"
                f"Settings: {mode_txt}, {mute_txt}, Remind {self._fmt_mmss(self.reminder_interval_s)}, "
                f"Idle {self._fmt_mmss(self.inactivity_limit_s)}, {fs_txt}"
            )

        # Active: show "worked so far" and "next stop in"
        now = time.time()
        worked_s = now - self.last_reminder_time
        remaining_s = max(0.0, self.reminder_interval_s - worked_s)

        worked_m = self._fmt_mins_only(worked_s)
        remaining_m = self._fmt_mins_only(remaining_s)

        return (
            f"Have been working for {worked_m} mins. Next stop in {remaining_m} mins.\n"
            f"Settings: {mode_txt}, {mute_txt}, Remind {self._fmt_mmss(self.reminder_interval_s)}, "
            f"Idle {self._fmt_mmss(self.inactivity_limit_s)}, {fs_txt}"
        )


    def _update_tooltip(self):
        self.tray.setToolTip(self._tooltip_text("Active" if self.active else "Inactive"))

    def _set_mode(self, mode: str):
        self.mode = mode
        self.action_voice.setChecked(mode == "voice")
        self.action_beep.setChecked(mode == "beep")
        self._update_tooltip()

    def _toggle_mute(self):
        self.muted = self.action_mute.isChecked()
        self._update_tooltip()

    def _toggle_fullscreen(self):
        self.disable_fullscreen = self.action_fullscreen.isChecked()
        self._update_tooltip()

    def _set_reminder_interval_minutes(self, minutes: float):
        self.reminder_interval_s = float(minutes) * 60.0
        self.last_reminder_time = time.time()  # avoid instant fire
        self._update_tooltip()

    def _set_inactivity_limit_minutes(self, minutes: float):
        self.inactivity_limit_s = float(minutes) * 60.0
        self._update_tooltip()

    def _set_active(self):
        if not self.active:
            self.active = True
            self.last_reminder_time = time.time()
            self.tray.setIcon(self.icon_active)
        self._update_tooltip()

    def _set_inactive(self):
        if self.active:
            self.active = False
            self.tray.setIcon(self.icon_inactive)
        # Reset reminder timer while inactive
        self.last_reminder_time = time.time()
        self._update_tooltip()

    def _do_reminder(self):
        if self.muted:
            return

        if self.mode == "beep":
            beep_chirp()
        else:
            speak_windows_tts(VOICE_TEXT)

    def _tick(self):
        idle = get_idle_time_seconds()

        if idle > self.inactivity_limit_s:
            self._set_inactive()
            return

        self._set_active()

        if self.disable_fullscreen and is_foreground_fullscreen():
            # Don't remind while fullscreen; keep pushing timer forward
            self.last_reminder_time = time.time()
            return

        now = time.time()
        if (now - self.last_reminder_time) >= self.reminder_interval_s:
            self._do_reminder()
            self.last_reminder_time = now
            
        self._update_tooltip()


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
