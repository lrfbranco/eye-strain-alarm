import time
import threading
import ctypes
import winsound
from PIL import Image
import pystray
from pystray import MenuItem as item

# ===============================
# CONFIG
# ===============================
INACTIVITY_LIMIT = 15 * 60      # 15 minutes
REMINDER_INTERVAL = 60 * 60     # 1 hour
BEEP_FREQ = 800
BEEP_DUR = 200

# ===============================
# WINDOWS IDLE TIME
# ===============================
class LASTINPUTINFO(ctypes.Structure):
    _fields_ = [("cbSize", ctypes.c_uint),
                ("dwTime", ctypes.c_uint)]

def get_idle_time():
    lii = LASTINPUTINFO()
    lii.cbSize = ctypes.sizeof(LASTINPUTINFO)
    ctypes.windll.user32.GetLastInputInfo(ctypes.byref(lii))
    millis = ctypes.windll.kernel32.GetTickCount() - lii.dwTime
    return millis / 1000.0

# ===============================
# APP STATE
# ===============================
class ScreenBreakApp:
    def __init__(self):
        self.active = False
        self.last_active_time = time.time()
        self.last_beep_time = time.time()

        # self.icon_active = Image.open("active.ico")
        # self.icon_inactive = Image.open("inactive.ico")
        self.icon_active = Image.new("RGBA", (64, 64), (0, 180, 0, 255))    # green square
        self.icon_inactive = Image.new("RGBA", (64, 64), (180, 180, 180, 255))  # gray square


        self.icon = pystray.Icon(
            "ScreenBreak",
            self.icon_inactive,
            menu=pystray.Menu(
                item("Quit", self.quit)
            )
        )

    def start(self):
        threading.Thread(target=self.monitor_loop, daemon=True).start()
        self.icon.run()

    def quit(self, icon, item):
        icon.stop()

    def monitor_loop(self):
        while True:
            idle = get_idle_time()

            if idle > INACTIVITY_LIMIT:
                self.set_inactive()
            else:
                self.set_active()

            time.sleep(5)

    def set_active(self):
        if not self.active:
            self.active = True
            self.last_active_time = time.time()
            self.last_beep_time = time.time()
            self.icon.icon = self.icon_active

        if time.time() - self.last_beep_time >= REMINDER_INTERVAL:
            winsound.Beep(BEEP_FREQ, BEEP_DUR)
            self.last_beep_time = time.time()

    def set_inactive(self):
        if self.active:
            self.active = False
            self.icon.icon = self.icon_inactive

# ===============================
# RUN
# ===============================
if __name__ == "__main__":
    ScreenBreakApp().start()
