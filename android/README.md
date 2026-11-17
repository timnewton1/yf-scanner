# YF Scan for Android

The scanner on a phone: the same reads as `scan.py` and `tui.py`, over the phone's Bluetooth to the ELM327 v1.5 BLE mini. No laptop in the car.

Tabs:

| Tab | What |
|---|---|
| Connect | Finds the BLE adapter and remembers it (like `.adapter`); **Demo** is the simulated Pilot. Shows the adapter's `type:`, ATI/AT@1/STI/ATRV and the GATT channel |
| Health | The `--report` health check, section by section; codes only; **Clear codes** (asks you to type `clear`) |
| Live | Drive logging with a big **MARK** button and quick words (whine, drone, clunk...); sets `drive` / `noise` / `all`, Honda reads on or off, Quick (the fast values every row, the rest every 10-60 s), a tag. The screen stays on; logging keeps going if it turns off (a notification shows it) |
| Mode 06 | All results, failed only, misfire counters |
| Info | VIN, module names and calibration IDs; supported PIDs; the Honda probe with raw bytes |
| Console | One read-only request (01 02 03 06 07 09 0A 22 only) to one module; optional raw adapter traffic |
| Logs | The CSVs on the phone; tap for the `summarize.py` summary; share the CSV or the summary |

Logs use the same columns as `scan.py`, so `summarize.py` on the laptop reads them too, and gives the same output as the Logs tab.

Read-only, the same as the rest of the repo: the one thing that changes the car is Clear codes, and it asks first.

## Build and install

On Linux (this repo's `setup/` style; no root, everything goes in `~/Android`):

```sh
setup/android-sdk.sh             # JDK 17, Android SDK, Gradle, build Python. The first run asks you to accept Google's SDK licenses
setup/android-sdk.sh --build     # Python tests + app/build/outputs/apk/debug/app-debug.apk
setup/android-sdk.sh --install   # build, then install on a phone plugged in by USB
```

For `--install`, turn on USB debugging on the phone (Settings > About phone, tap Build number 7 times; then Developer options > USB debugging) and accept the prompt when you plug it in. Or copy `app-debug.apk` to the phone and open it (allow installs from that app when asked).

Android 8 or newer, with Bluetooth LE. On first Connect the app asks for the Bluetooth permission (Android 12+: "Nearby devices"; Android 8-11: location, which those versions require for BLE scans).

## Code

The app runs the repo's own Python on the phone: `scanner/` and `summarize.py`, embedded with [Chaquopy](https://chaquo.com/chaquopy/) and copied in at build time. A drive log, a health check or a summary on the phone comes from the same code as `scan.py` and `tui.py`, and so does DEMO (`scanner/sim/`). Kotlin does only what needs Android:

```
scanner/phone.py   the app's entry point into the scanner: connect, reads, drive log, summary (JSON out)
scanner/report.py  what each read shows, as sections of lines: the cards here, scan.py's output, the TUI's panes

android/app/.../Py.kt        the bridge: the Link the Python session reads through, the hooks it calls back on, the JSON model
android/app/.../BleLink.kt   BLE to the adapter (bleak has no Android backend under Chaquopy), using ble.py's GATT layouts
android/app/.../Scanner.kt   state, and the one worker thread that calls Python (one request on the adapter at a time)
android/app/.../Ui.kt        Compose screens; LogService.kt keeps a log going with the screen off
```

A change to what the scanner reads or shows goes in the Python and reaches the laptop and the phone alike. Tests: `uv run python -m unittest discover -s tests`. `tests/test_phone.py` drives `phone.py` with a fake BLE link and fake app hooks: connect, health, a refused write, and a log whose link drops, comes back and gets summarized. The build (`setup/android-sdk.sh --build`) runs them first.

### Across the Python–Kotlin line

What Kotlin hands Python arrives as Java objects. These rules come from bugs the laptop tests missed:

- **A Kotlin `List` is a `java.util.ArrayList`, which Python can't iterate.** Call `.toArray()` first (`LinkPort.info`). A `ByteArray` holds signed bytes: `bytes(arr)` is right, iterating it gives -128..127.
- **A Java exception's `str()` starts with its class name.** Use `phone._msg(e)` (its `getMessage()`) for text a person reads.
- **Kotlin parameters are typed and non-null.** `None` for a `String` is a NullPointerException, and a float for a `Long` matches no overload.
- **Display hooks (`traffic`, `section`, `row`, `status`) go through `phone._tell`.** They run UI code in the middle of a read or a drive log, and an exception from them must not end either.
- **Once a link is open, close it on every failure.** The adapter takes one BLE connection at a time, so a leaked link refuses the next connect.
- **Hooks run on the adapter thread.** State the screens change too goes through `MutableStateFlow.update {}`.
- **Kotlin's catches log with `Log.e("yf", ...)`.** A `PyException`'s stack trace carries the Python traceback (`adb logcat -s yf python.stderr`).

The fakes in `tests/test_phone.py` enforce the first three, so a slip there fails on the laptop.

The build needs a Python 3.14 on the build machine (Chaquopy compiles the scanner with it). `setup/android-sdk.sh` gets one through uv and writes its path to `android/local.properties`. The APK carries Python for arm64 phones and the x86_64 emulator.
