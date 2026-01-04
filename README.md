# Eye Strain Alarm

This is a simple Python app to remind myself to blink and look away from screens frequently. Feel free to use if you like.

<img src="https://github.com/lrfbranco/eye-strain-alarm/blob/main/etc/image.png?raw=true" width="350">

## Features

- ✅ Lives as **tray icon**
- ✅ Beep or voice-to-text message
- ✅ Stops tracking if user is inactive
- ✅ Pre programmed time intervals. Default will beep every 1 hour of work.
- ✅ Mute option
- ✅ Ignore fullscreen apps (games, videos, movies, etc)
- ✅ Dark mode enabled

## How-to's
- Simply running **./dist/eye-strain-alarm.exe** will start the app in the tray menu. If you want to compile it yourself, see below.
- Green rectangle is tracking, gray rectangle is inactive (due to mouse movement).
- I recommend setting up Task Scheduler and run this on boot: 

1- Open **Task Scheduler** > Create Task… (not “Basic Task”)

Name: eye-strain-alarm

Check: Run only when user is logged on
Check: Run with highest privileges (optional; helps with some fullscreen detection edge cases)

2- Setup **Triggers**

New… → At log on

Optional: “Delay task for” 1 minute

3- Setup **Actions**

New… → Start a program

Program location: *C:\path\to\eye-strain-alarm.exe*

4- Setup **Conditions**

Uncheck: “Start the task only if the computer is on AC power” (if on laptop)

5- Save.


## Compilation, **Python 3.14.0**
In case you want to compile this yourself, you'll need these libs:
```javascript
pip install pyside6 pyinstaller
```

Compile with:
```
pyinstaller --noconsole --onefile main.py
```
.exe will show up at /dist/main.exe

---

*To healthy eyes!* 👁️👁️
