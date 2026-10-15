#!/usr/bin/env python3
"""Cross-platform Push-to-Talk dictation daemon using whisper.cpp.

Hold F5 for Russian, F6 for English — release to paste transcribed text at cursor.

Supported platforms:
  * macOS  — global hotkeys & paste via Quartz, sound via `afplay`, clipboard via `pbcopy`.
  * Windows — global hotkeys & paste via `pynput`, sound via `winsound`, clipboard via `pynput`+ctypes.

The platform is auto-detected at runtime; the correct backend is selected automatically.
"""

from __future__ import annotations

import atexit
import logging
import re
import subprocess
import sys
import tempfile
import threading
import time
import wave
from pathlib import Path
from typing import Callable, Optional

import pyaudio

logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger(__name__)

IS_MAC = sys.platform == "darwin"
IS_WINDOWS = sys.platform.startswith("win")
IS_LINUX = sys.platform.startswith("linux")

# ---------------------------------------------------------------------------
# Shared configuration
# ---------------------------------------------------------------------------

SAMPLE_RATE = 16000
CHANNELS = 1
CHUNK_SIZE = 1024

MIN_RECORDING_DURATION = 0.4  # seconds

# Logical hotkey names -> language. Backends map these to real keys.
HOTKEY_LANGS: dict[str, str] = {
    "f5": "ru",
    "f6": "en",
}

_HERE = Path(__file__).parent


def _is_ascii_path(p) -> bool:
    """True if the path contains only ASCII characters.

    whisper.cpp builds for Windows cannot open files whose path contains
    non-ASCII characters (e.g. a Cyrillic user name) and crash hard
    (STATUS_STACK_BUFFER_OVERRUN, exit code 0xC0000409). We detect this so we
    can fall back to an ASCII-only location.
    """
    try:
        str(p).encode("ascii")
        return True
    except (UnicodeEncodeError, UnicodeDecodeError):
        return False


# ASCII-only working directory used on Windows when the default locations
# (%LOCALAPPDATA%, the temp dir) contain non-ASCII characters. whisper.cpp
# cannot handle those paths, so we keep its binaries, model and the temporary
# WAV here instead.
ASCII_WORKDIR = Path(r"C:\whisper-cpp") if IS_WINDOWS else None

HALLUCINATIONS: frozenset[str] = frozenset({
    "you",
    "thank you",
    "thanks for watching",
    "silence",
    "продолжение следует",
    "субтитры сделал didbyrevol",
})


# ---------------------------------------------------------------------------
# Platform backend abstraction
# ---------------------------------------------------------------------------

class PlatformBackend:
    """Abstract interface for the platform-specific pieces of the daemon."""

    # Paths to the whisper.cpp CLI and model, resolved per platform.
    whisper_cli: str
    model_path: Path

    def validate(self) -> None:
        """Raise SystemExit if whisper-cli or the model are missing."""
        if not Path(self.whisper_cli).is_file():
            log.error("whisper-cli not found at %s", self.whisper_cli)
            self._whisper_install_hint()
            raise SystemExit(1)
        if not self.model_path.is_file():
            log.error(
                "Model not found at %s — download from https://huggingface.co/ggerganov/whisper.cpp",
                self.model_path,
            )
            raise SystemExit(1)

    def _whisper_install_hint(self) -> None:  # pragma: no cover - informational
        pass

    def play_sound(self, which: str) -> None:
        """Play a notification sound. `which` is 'begin' or 'end'."""
        raise NotImplementedError

    def copy_to_clipboard(self, text: str) -> None:
        raise NotImplementedError

    def send_paste(self) -> None:
        """Simulate the paste shortcut (Cmd+V / Ctrl+V)."""
        raise NotImplementedError

    def type_text(self, text: str) -> None:
        """Insert `text` by typing it directly (no clipboard).

        Default implementation falls back to clipboard + paste shortcut.
        Backends may override with a direct key-injection approach.
        """
        self.copy_to_clipboard(text)
        time.sleep(0.05)
        self.send_paste()

    def paste_text(self, text: str) -> None:
        self.type_text(text)
        log.info("Pasted: %s", text)

    def run_event_loop(
        self,
        on_key_down: Callable[[str], None],
        on_key_up: Callable[[], None],
    ) -> None:
        """Start listening for the configured hotkeys and block forever.

        `on_key_down(lang)` is called when a hotkey is pressed,
        `on_key_up()` when it is released.
        """
        raise NotImplementedError


# ---------------------------------------------------------------------------
# macOS backend (Quartz + pbcopy + afplay)
# ---------------------------------------------------------------------------

class MacBackend(PlatformBackend):
    # macOS keycodes for the hotkeys.
    _KEYCODE_LANGS = {
        0x60: "ru",  # F5
        0x61: "en",  # F6
    }

    def __init__(self) -> None:
        self.whisper_cli = "/opt/homebrew/bin/whisper-cli"
        self.model_path = Path.home() / ".local/share/whisper-cpp/ggml-large-v3-turbo.bin"
        self._sound_begin = _HERE / "sounds" / "begin_record.caf"
        self._sound_end = _HERE / "sounds" / "end_record.caf"
        self._pbcopy = "/usr/bin/pbcopy"

    def _whisper_install_hint(self) -> None:
        log.error("Run: brew install whisper-cpp")

    def play_sound(self, which: str) -> None:
        path = self._sound_begin if which == "begin" else self._sound_end
        if not path.is_file():
            return
        subprocess.Popen(
            ["afplay", str(path)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

    def copy_to_clipboard(self, text: str) -> None:
        subprocess.run([self._pbcopy], input=text.encode(), check=True)

    def send_paste(self) -> None:
        import Quartz

        src = Quartz.CGEventSourceCreate(Quartz.kCGEventSourceStateCombinedSessionState)
        for is_down in (True, False):
            event = Quartz.CGEventCreateKeyboardEvent(src, 0x09, is_down)  # 'v'
            Quartz.CGEventSetFlags(event, Quartz.kCGEventFlagMaskCommand)
            Quartz.CGEventPost(Quartz.kCGAnnotatedSessionEventTap, event)

    def run_event_loop(self, on_key_down, on_key_up) -> None:
        import Quartz
        from Quartz import (
            CFMachPortCreateRunLoopSource,
            CFRunLoopAddSource,
            CFRunLoopGetCurrent,
            CFRunLoopRun,
            CGEventGetIntegerValueField,
            CGEventMaskBit,
            CGEventTapCreate,
            kCFRunLoopCommonModes,
            kCGEventKeyDown,
            kCGEventKeyUp,
            kCGHeadInsertEventTap,
            kCGKeyboardEventKeycode,
            kCGSessionEventTap,
        )

        def event_callback(proxy, event_type, event, refcon):
            keycode = CGEventGetIntegerValueField(event, kCGKeyboardEventKeycode)
            log.debug("KEY event_type=%s keycode=0x%x (%d)", event_type, keycode, keycode)

            if keycode not in self._KEYCODE_LANGS:
                return event

            if event_type == kCGEventKeyDown:
                on_key_down(self._KEYCODE_LANGS[keycode])
                return None
            if event_type == kCGEventKeyUp:
                on_key_up()
                return None
            return event

        mask = CGEventMaskBit(kCGEventKeyDown) | CGEventMaskBit(kCGEventKeyUp)
        tap = CGEventTapCreate(
            kCGSessionEventTap,
            kCGHeadInsertEventTap,
            0,
            mask,
            event_callback,
            None,
        )
        if tap is None:
            log.error("Failed to create event tap — grant Accessibility in System Settings")
            raise SystemExit(1)

        source = CFMachPortCreateRunLoopSource(None, tap, 0)
        CFRunLoopAddSource(CFRunLoopGetCurrent(), source, kCFRunLoopCommonModes)
        Quartz.CGEventTapEnable(tap, True)

        def watchdog(_timer, _info):
            if not Quartz.CGEventTapIsEnabled(tap):
                log.warning("Event tap disabled by macOS, re-enabling...")
                Quartz.CGEventTapEnable(tap, True)

        timer = Quartz.CFRunLoopTimerCreate(
            None,
            Quartz.CFAbsoluteTimeGetCurrent() + 5.0,
            5.0,
            0,
            0,
            watchdog,
            None,
        )
        Quartz.CFRunLoopAddTimer(CFRunLoopGetCurrent(), timer, kCFRunLoopCommonModes)

        log.info("Push-to-Talk ready — F5=Russian, F6=English")
        CFRunLoopRun()


# ---------------------------------------------------------------------------
# Windows / Linux backend (pynput + winsound/clipboard)
# ---------------------------------------------------------------------------

class PynputBackend(PlatformBackend):
    """Backend based on the cross-platform `pynput` library.

    Used on Windows (and usable on Linux). Hotkeys and paste are simulated
    through pynput; the clipboard and sound use OS-native facilities.
    """

    def __init__(self) -> None:
        self._sound_begin = _HERE / "sounds" / "begin_record.wav"
        self._sound_end = _HERE / "sounds" / "end_record.wav"
        self.whisper_cli = self._find_whisper_cli()
        self.model_path = self._default_model_path()
        self._pressed = False  # debounce auto-repeat key-down events

    # -- whisper.cpp discovery ------------------------------------------------

    def _default_model_path(self) -> Path:
        if IS_WINDOWS:
            import os

            base = os.environ.get("LOCALAPPDATA", str(Path.home()))
            default = Path(base) / "whisper-cpp" / "ggml-large-v3-turbo.bin"
            # whisper.cpp can't open non-ASCII paths; prefer an ASCII location.
            if ASCII_WORKDIR is not None and not _is_ascii_path(default):
                ascii_model = ASCII_WORKDIR / "ggml-large-v3-turbo.bin"
                if ascii_model.is_file():
                    return ascii_model
            return default
        return Path.home() / ".local/share/whisper-cpp/ggml-large-v3-turbo.bin"

    def _find_whisper_cli(self) -> str:
        import os
        import shutil

        # 1) Explicit override via environment variable.
        env = os.environ.get("WHISPER_CLI")
        if env and Path(env).is_file():
            return env

        # 2) On PATH. On Windows search only explicit whisper names with the
        #    .exe suffix — a bare "main" would match system files such as
        #    main.cpl via PATHEXT and produce a false positive.
        if IS_WINDOWS:
            names = ["whisper-cli.exe", "whisper-cpp.exe"]
        else:
            names = ["whisper-cli", "whisper-cpp", "main"]
        for name in names:
            found = shutil.which(name)
            if found:
                return found

        # 3) Common install locations.
        candidates: list[Path] = []
        if IS_WINDOWS:
            base = os.environ.get("LOCALAPPDATA", str(Path.home()))
            program_files = os.environ.get("ProgramFiles", r"C:\Program Files")
            # whisper.cpp crashes on non-ASCII paths. If the usual base is
            # non-ASCII (e.g. a Cyrillic user name), look in an ASCII-only
            # location first.
            if ASCII_WORKDIR is not None and not _is_ascii_path(base):
                candidates.append(ASCII_WORKDIR / "whisper-cli.exe")
            candidates += [
                Path(base) / "whisper-cpp" / "whisper-cli.exe",
                Path(base) / "whisper-cpp" / "main.exe",
                Path(program_files) / "whisper-cpp" / "whisper-cli.exe",
                Path.home() / "whisper-cpp" / "whisper-cli.exe",
                Path.home() / "whisper.cpp" / "build" / "bin" / "Release" / "whisper-cli.exe",
                Path.home() / "whisper.cpp" / "build" / "bin" / "whisper-cli.exe",
            ]
        else:
            candidates += [
                Path("/usr/local/bin/whisper-cli"),
                Path("/usr/bin/whisper-cli"),
                Path.home() / "whisper.cpp" / "build" / "bin" / "whisper-cli",
            ]
        for c in candidates:
            if c.is_file():
                return str(c)

        # Fall back to a sensible default name; validate() will report the error.
        return "whisper-cli.exe" if IS_WINDOWS else "whisper-cli"

    def _whisper_install_hint(self) -> None:
        if IS_WINDOWS:
            log.error(
                "Install whisper.cpp and either put whisper-cli.exe on PATH, "
                "set the WHISPER_CLI environment variable, or place it in "
                "%%LOCALAPPDATA%%\\whisper-cpp\\. "
                "Prebuilt binaries: https://github.com/ggerganov/whisper.cpp/releases"
            )
        else:
            log.error("Build whisper.cpp or install it so that whisper-cli is on PATH.")

    # -- sound ----------------------------------------------------------------

    def play_sound(self, which: str) -> None:
        path = self._sound_begin if which == "begin" else self._sound_end
        if not path.is_file():
            return
        if IS_WINDOWS:
            import winsound

            # SND_ASYNC: non-blocking; SND_FILENAME: `path` is a file name.
            winsound.PlaySound(str(path), winsound.SND_FILENAME | winsound.SND_ASYNC)
        else:
            # Linux fallback: try common players, ignore failures.
            for player in ("aplay", "paplay", "ffplay"):
                import shutil

                if shutil.which(player):
                    args = [player, str(path)]
                    if player == "ffplay":
                        args = [player, "-nodisp", "-autoexit", "-loglevel", "quiet", str(path)]
                    subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                    return

    # -- clipboard & paste ----------------------------------------------------

    def copy_to_clipboard(self, text: str) -> None:
        if IS_WINDOWS:
            self._win_set_clipboard(text)
        else:
            # Try xclip/xsel on Linux.
            import shutil

            if shutil.which("xclip"):
                subprocess.run(["xclip", "-selection", "clipboard"], input=text.encode(), check=True)
            elif shutil.which("xsel"):
                subprocess.run(["xsel", "--clipboard", "--input"], input=text.encode(), check=True)
            else:
                raise RuntimeError("No clipboard tool found (install xclip or xsel)")

    @staticmethod
    def _win_set_clipboard(text: str) -> None:
        import ctypes
        from ctypes import wintypes

        CF_UNICODETEXT = 13
        GMEM_MOVEABLE = 0x0002

        kernel32 = ctypes.windll.kernel32
        user32 = ctypes.windll.user32

        kernel32.GlobalAlloc.restype = wintypes.HGLOBAL
        kernel32.GlobalAlloc.argtypes = [wintypes.UINT, ctypes.c_size_t]
        kernel32.GlobalLock.restype = wintypes.LPVOID
        kernel32.GlobalLock.argtypes = [wintypes.HGLOBAL]
        kernel32.GlobalUnlock.argtypes = [wintypes.HGLOBAL]
        user32.SetClipboardData.restype = wintypes.HANDLE
        user32.SetClipboardData.argtypes = [wintypes.UINT, wintypes.HANDLE]

        data = text.encode("utf-16-le") + b"\x00\x00"
        size = len(data)

        if not user32.OpenClipboard(None):
            raise RuntimeError("Failed to open clipboard")
        try:
            user32.EmptyClipboard()
            handle = kernel32.GlobalAlloc(GMEM_MOVEABLE, size)
            if not handle:
                raise RuntimeError("GlobalAlloc failed")
            ptr = kernel32.GlobalLock(handle)
            if not ptr:
                raise RuntimeError("GlobalLock failed")
            try:
                ctypes.memmove(ptr, data, size)
            finally:
                kernel32.GlobalUnlock(handle)
            if not user32.SetClipboardData(CF_UNICODETEXT, handle):
                raise RuntimeError("SetClipboardData failed")
        finally:
            user32.CloseClipboard()

    def send_paste(self) -> None:
        if IS_WINDOWS:
            # Send Ctrl+V via the Win32 SendInput API using virtual-key codes.
            # Layout-independent (pynput's Controller.press("v") can fail when a
            # non-Latin layout such as Russian is active) and unaffected by our
            # own low-level keyboard hook.
            import ctypes
            from ctypes import wintypes

            VK_CONTROL = 0x11
            VK_V = 0x56
            KEYEVENTF_KEYUP = 0x0002
            INPUT_KEYBOARD = 1
            ULONG_PTR = ctypes.POINTER(ctypes.c_ulong)

            class KEYBDINPUT(ctypes.Structure):
                _fields_ = [
                    ("wVk", wintypes.WORD),
                    ("wScan", wintypes.WORD),
                    ("dwFlags", wintypes.DWORD),
                    ("time", wintypes.DWORD),
                    ("dwExtraInfo", ULONG_PTR),
                ]

            class MOUSEINPUT(ctypes.Structure):
                _fields_ = [
                    ("dx", wintypes.LONG),
                    ("dy", wintypes.LONG),
                    ("mouseData", wintypes.DWORD),
                    ("dwFlags", wintypes.DWORD),
                    ("time", wintypes.DWORD),
                    ("dwExtraInfo", ULONG_PTR),
                ]

            class HARDWAREINPUT(ctypes.Structure):
                _fields_ = [
                    ("uMsg", wintypes.DWORD),
                    ("wParamL", wintypes.WORD),
                    ("wParamH", wintypes.WORD),
                ]

            class _INPUTUNION(ctypes.Union):
                _fields_ = [
                    ("ki", KEYBDINPUT),
                    ("mi", MOUSEINPUT),
                    ("hi", HARDWAREINPUT),
                ]

            class INPUT(ctypes.Structure):
                _fields_ = [("type", wintypes.DWORD), ("u", _INPUTUNION)]

            user32 = ctypes.windll.user32
            user32.SendInput.argtypes = (
                wintypes.UINT,
                ctypes.POINTER(INPUT),
                ctypes.c_int,
            )
            user32.SendInput.restype = wintypes.UINT

            def _key(vk, up):
                i = INPUT()
                i.type = INPUT_KEYBOARD
                i.u.ki = KEYBDINPUT(
                    vk, 0, KEYEVENTF_KEYUP if up else 0, 0, None
                )
                return i

            seq = (INPUT * 4)(
                _key(VK_CONTROL, False),
                _key(VK_V, False),
                _key(VK_V, True),
                _key(VK_CONTROL, True),
            )
            user32.SendInput(4, seq, ctypes.sizeof(INPUT))
            return

        from pynput.keyboard import Controller, Key

        kb = Controller()
        modifier = Key.cmd if IS_MAC else Key.ctrl
        with kb.pressed(modifier):
            kb.press("v")
            kb.release("v")

    def type_text(self, text: str) -> None:
        """Type `text` directly, character by character.

        On Windows this uses SendInput with KEYEVENTF_UNICODE, which injects
        the actual characters rather than a Ctrl+V paste. This works in every
        target window — including the classic console (conhost / cmd.exe /
        legacy PowerShell), where Ctrl+V does not paste from the clipboard —
        and is independent of the active keyboard layout.
        """
        if not IS_WINDOWS:
            # Non-Windows: fall back to clipboard + paste shortcut.
            self.copy_to_clipboard(text)
            time.sleep(0.05)
            self.send_paste()
            return

        import ctypes
        from ctypes import wintypes

        KEYEVENTF_KEYUP = 0x0002
        KEYEVENTF_UNICODE = 0x0004
        INPUT_KEYBOARD = 1
        ULONG_PTR = ctypes.POINTER(ctypes.c_ulong)

        class KEYBDINPUT(ctypes.Structure):
            _fields_ = [
                ("wVk", wintypes.WORD),
                ("wScan", wintypes.WORD),
                ("dwFlags", wintypes.DWORD),
                ("time", wintypes.DWORD),
                ("dwExtraInfo", ULONG_PTR),
            ]

        class MOUSEINPUT(ctypes.Structure):
            _fields_ = [
                ("dx", wintypes.LONG),
                ("dy", wintypes.LONG),
                ("mouseData", wintypes.DWORD),
                ("dwFlags", wintypes.DWORD),
                ("time", wintypes.DWORD),
                ("dwExtraInfo", ULONG_PTR),
            ]

        class HARDWAREINPUT(ctypes.Structure):
            _fields_ = [
                ("uMsg", wintypes.DWORD),
                ("wParamL", wintypes.WORD),
                ("wParamH", wintypes.WORD),
            ]

        class _INPUTUNION(ctypes.Union):
            _fields_ = [
                ("ki", KEYBDINPUT),
                ("mi", MOUSEINPUT),
                ("hi", HARDWAREINPUT),
            ]

        class INPUT(ctypes.Structure):
            _fields_ = [("type", wintypes.DWORD), ("u", _INPUTUNION)]

        user32 = ctypes.windll.user32
        user32.SendInput.argtypes = (
            wintypes.UINT,
            ctypes.POINTER(INPUT),
            ctypes.c_int,
        )
        user32.SendInput.restype = wintypes.UINT

        def _unicode_key(code_unit: int, up: bool) -> INPUT:
            i = INPUT()
            i.type = INPUT_KEYBOARD
            flags = KEYEVENTF_UNICODE | (KEYEVENTF_KEYUP if up else 0)
            # wVk must be 0 for a Unicode event; the character goes in wScan.
            i.u.ki = KEYBDINPUT(0, code_unit, flags, 0, None)
            return i

        # Build a key-down + key-up event for each UTF-16 code unit. Characters
        # outside the BMP (e.g. emoji) span two surrogate code units, which
        # SendInput handles natively when sent in sequence.
        events: list[INPUT] = []
        utf16 = text.encode("utf-16-le")
        for idx in range(0, len(utf16), 2):
            code_unit = utf16[idx] | (utf16[idx + 1] << 8)
            events.append(_unicode_key(code_unit, up=False))
            events.append(_unicode_key(code_unit, up=True))

        if not events:
            return

        arr = (INPUT * len(events))(*events)
        user32.SendInput(len(events), arr, ctypes.sizeof(INPUT))

    # -- event loop -----------------------------------------------------------

    def run_event_loop(self, on_key_down, on_key_up) -> None:
        from pynput import keyboard

        key_to_lang = {
            keyboard.Key.f5: "ru",
            keyboard.Key.f6: "en",
        }

        def on_press(key):
            lang = key_to_lang.get(key)
            if lang is None:
                return
            if self._pressed:  # ignore OS auto-repeat
                return
            self._pressed = True
            on_key_down(lang)

        def on_release(key):
            if key not in key_to_lang:
                return
            if not self._pressed:
                return
            self._pressed = False
            on_key_up()

        log.info("Push-to-Talk ready — F5=Russian, F6=English")

        if IS_WINDOWS:
            # On Windows we drive everything from the low-level hook filter so
            # we can BOTH react to the key AND suppress it, preventing the
            # foreground app (e.g. Notepad, which inserts the date/time on F5)
            # from receiving it.
            #
            # Note: pynput's on_press/on_release callbacks are NOT delivered for
            # an event once suppress_event() is called (it raises inside the
            # hook before the event is posted to the processing loop), so the
            # press/release logic lives here instead.
            WM_KEYDOWN, WM_SYSKEYDOWN = 0x0100, 0x0104
            WM_KEYUP, WM_SYSKEYUP = 0x0101, 0x0105
            VK_TO_LANG = {0x74: "ru", 0x75: "en"}  # F5, F6

            def win32_event_filter(msg, data):
                lang = VK_TO_LANG.get(data.vkCode)
                if lang is None:
                    return True  # not our key: let it through untouched

                if msg in (WM_KEYDOWN, WM_SYSKEYDOWN):
                    if not self._pressed:  # ignore OS auto-repeat
                        self._pressed = True
                        on_key_down(lang)
                elif msg in (WM_KEYUP, WM_SYSKEYUP):
                    if self._pressed:
                        self._pressed = False
                        on_key_up()

                listener.suppress_event()  # swallow the key system-wide
                return True

            with keyboard.Listener(
                on_press=lambda key: None,
                on_release=lambda key: None,
                win32_event_filter=win32_event_filter,
            ) as listener:
                listener.join()
        else:
            with keyboard.Listener(on_press=on_press, on_release=on_release) as listener:
                listener.join()


# ---------------------------------------------------------------------------
# Backend factory
# ---------------------------------------------------------------------------

def make_backend() -> PlatformBackend:
    if IS_MAC:
        return MacBackend()
    if IS_WINDOWS or IS_LINUX:
        return PynputBackend()
    log.error("Unsupported platform: %s", sys.platform)
    raise SystemExit(1)


# ---------------------------------------------------------------------------
# Audio recording (PyAudio — instant start, no process spawn)
# ---------------------------------------------------------------------------

class Recorder:
    def __init__(self) -> None:
        self._pa = pyaudio.PyAudio()
        self._stream: Optional[pyaudio.Stream] = None
        self._frames: list[bytes] = []
        self._start_time: float = 0.0
        self._recording = False
        atexit.register(self._cleanup)

    def _cleanup(self) -> None:
        if self._stream:
            self._stream.close()
        self._pa.terminate()

    def start(self) -> None:
        if self._recording:
            return

        self._frames = []
        self._start_time = time.monotonic()
        self._recording = True

        self._stream = self._pa.open(
            format=pyaudio.paInt16,
            channels=CHANNELS,
            rate=SAMPLE_RATE,
            input=True,
            frames_per_buffer=CHUNK_SIZE,
            stream_callback=self._audio_callback,
        )
        log.info("Recording started")

    def _audio_callback(self, in_data, frame_count, time_info, status):
        if self._recording:
            self._frames.append(in_data)
        return (None, pyaudio.paContinue)

    def stop(self) -> Optional[Path]:
        if not self._recording:
            return None

        self._recording = False

        if self._stream:
            self._stream.stop_stream()
            self._stream.close()
            self._stream = None

        duration = time.monotonic() - self._start_time
        if duration < MIN_RECORDING_DURATION:
            log.info("Recording too short (%.2fs), ignoring", duration)
            return None

        import os

        # whisper.cpp cannot read files from a non-ASCII path and crashes.
        # Put the temporary WAV in an ASCII-only directory when the system
        # temp dir contains non-ASCII characters (e.g. a Cyrillic user name).
        tmp_dir = tempfile.gettempdir()
        if ASCII_WORKDIR is not None and not _is_ascii_path(tmp_dir):
            tmp_dir = str(ASCII_WORKDIR)
            os.makedirs(tmp_dir, exist_ok=True)

        fd, name = tempfile.mkstemp(suffix=".wav", dir=tmp_dir)
        os.close(fd)
        temp_file = Path(name)
        with wave.open(str(temp_file), "wb") as wf:
            wf.setnchannels(CHANNELS)
            wf.setsampwidth(2)
            wf.setframerate(SAMPLE_RATE)
            wf.writeframes(b"".join(self._frames))

        log.info("Recording stopped (%.2fs, %d bytes)", duration, temp_file.stat().st_size)
        return temp_file


# ---------------------------------------------------------------------------
# Transcription
# ---------------------------------------------------------------------------

def transcribe(backend: PlatformBackend, wav_path: Path, lang: str) -> Optional[str]:
    log.info("Transcribing (%s)...", lang)

    try:
        result = subprocess.run(
            [
                backend.whisper_cli,
                "-m",
                str(backend.model_path),
                "-f",
                str(wav_path),
                "--no-timestamps",
                "-t",
                "4",
                "-l",
                lang,
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )

        if result.returncode != 0:
            log.error("whisper-cli failed: %s", result.stderr.strip())
            return None

        raw = result.stdout.strip()
        log.debug("Whisper raw output: %r", raw)

        text = re.sub(r"\[.*?\]", "", raw).strip()
        text = re.sub(r"^[-\s]+", "", text).strip()

        if not text:
            log.info("Empty output, ignoring")
            return None

        if text.lower().strip(" .!-") in HALLUCINATIONS:
            log.info("Hallucination detected, ignoring: %r", text)
            return None

        return text
    finally:
        if wav_path.exists():
            wav_path.unlink()


# ---------------------------------------------------------------------------
# Main application
# ---------------------------------------------------------------------------

class PushToTalkApp:
    def __init__(self) -> None:
        self._backend = make_backend()
        self._recorder = Recorder()
        self._lock = threading.Lock()
        self._recording = False
        self._active_lang = "auto"

    def _on_key_down(self, lang: str) -> None:
        with self._lock:
            if self._recording:
                return
            self._recording = True
            self._active_lang = lang
        self._backend.play_sound("begin")
        self._recorder.start()

    def _on_key_up(self) -> None:
        with self._lock:
            if not self._recording:
                return
            self._recording = False
            lang = self._active_lang
        self._backend.play_sound("end")
        threading.Thread(target=self._process, args=(lang,), daemon=True).start()

    def _process(self, lang: str) -> None:
        wav = self._recorder.stop()
        if wav is None:
            return
        text = transcribe(self._backend, wav, lang)
        if text:
            self._backend.paste_text(text)

    def run(self) -> None:
        self._backend.validate()
        self._backend.run_event_loop(self._on_key_down, self._on_key_up)


if __name__ == "__main__":
    PushToTalkApp().run()
