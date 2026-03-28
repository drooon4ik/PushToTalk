#!/usr/bin/env python3
"""macOS Push-to-Talk dictation daemon using whisper.cpp.

Hold F5 for Russian, F6 for English — release to paste transcribed text at cursor.
"""

from __future__ import annotations

import logging
import os
import re
import subprocess
import tempfile
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

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

logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

FFMPEG = "/opt/homebrew/bin/ffmpeg"
PBCOPY = "/usr/bin/pbcopy"
WHISPER_CLI = "/opt/homebrew/bin/whisper-cli"
MODEL_PATH = Path.home() / ".local/share/whisper-cpp/ggml-large-v3-turbo.bin"
RECORD_PATH = Path(tempfile.gettempdir()) / "dictation.wav"

MIN_RECORDING_DURATION = 0.4  # seconds

HOTKEYS: dict[int, str] = {
    0x60: "ru",  # F5
    0x61: "en",  # F6
}

HALLUCINATIONS: frozenset[str] = frozenset({
    "you",
    "thank you",
    "thanks for watching",
    "silence",
    "продолжение следует",
    "субтитры сделал didbyrevol",
})

# ---------------------------------------------------------------------------
# Setup validation
# ---------------------------------------------------------------------------

def validate_setup() -> None:
    if not Path(WHISPER_CLI).is_file():
        log.error("whisper-cli not found at %s — run: brew install whisper-cpp", WHISPER_CLI)
        raise SystemExit(1)
    if not MODEL_PATH.is_file():
        log.error("Model not found at %s — download from https://huggingface.co/ggerganov/whisper.cpp", MODEL_PATH)
        raise SystemExit(1)

# ---------------------------------------------------------------------------
# Audio recording
# ---------------------------------------------------------------------------

class Recorder:
    def __init__(self) -> None:
        self._proc: Optional[subprocess.Popen] = None
        self._start_time: float = 0.0

    def start(self) -> None:
        self._start_time = time.monotonic()
        self._proc = subprocess.Popen(
            [
                FFMPEG, "-y",
                "-f", "avfoundation", "-i", ":default",
                "-ar", "16000", "-ac", "1", "-sample_fmt", "s16",
                str(RECORD_PATH),
            ],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        log.info("Recording started")

    def stop(self) -> Optional[Path]:
        if self._proc is None:
            return None

        self._proc.terminate()
        self._proc.wait()
        self._proc = None

        duration = time.monotonic() - self._start_time
        if duration < MIN_RECORDING_DURATION:
            log.info("Recording too short (%.2fs), ignoring", duration)
            return None

        log.info("Recording stopped (%.2fs)", duration)
        return RECORD_PATH

# ---------------------------------------------------------------------------
# Transcription
# ---------------------------------------------------------------------------

def transcribe(wav_path: Path, lang: str) -> Optional[str]:
    log.info("Transcribing (%s)...", lang)

    result = subprocess.run(
        [WHISPER_CLI, "-m", str(MODEL_PATH), "-f", str(wav_path), "--no-timestamps", "-t", "4", "-l", lang],
        capture_output=True,
        text=True,
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

# ---------------------------------------------------------------------------
# Paste
# ---------------------------------------------------------------------------

def paste_text(text: str) -> None:
    subprocess.run([PBCOPY], input=text.encode(), check=True)
    time.sleep(0.05)

    src = Quartz.CGEventSourceCreate(Quartz.kCGEventSourceStateCombinedSessionState)
    for is_down in (True, False):
        event = Quartz.CGEventCreateKeyboardEvent(src, 0x09, is_down)
        Quartz.CGEventSetFlags(event, Quartz.kCGEventFlagMaskCommand)
        Quartz.CGEventPost(Quartz.kCGAnnotatedSessionEventTap, event)

    log.info("Pasted: %s", text)

# ---------------------------------------------------------------------------
# Main application
# ---------------------------------------------------------------------------

class PushToTalkApp:
    def __init__(self) -> None:
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
        self._recorder.start()

    def _on_key_up(self) -> None:
        with self._lock:
            if not self._recording:
                return
            self._recording = False
            lang = self._active_lang
        threading.Thread(target=self._process, args=(lang,), daemon=True).start()

    def _process(self, lang: str) -> None:
        wav = self._recorder.stop()
        if wav is None:
            return
        text = transcribe(wav, lang)
        if text:
            paste_text(text)

    def _event_callback(self, proxy, event_type, event, refcon):
        keycode = CGEventGetIntegerValueField(event, kCGKeyboardEventKeycode)

        if keycode not in HOTKEYS:
            return event

        if event_type == kCGEventKeyDown:
            self._on_key_down(HOTKEYS[keycode])
            return None

        if event_type == kCGEventKeyUp:
            self._on_key_up()
            return None

        return event

    def _watchdog(self, tap, _timer, _info) -> None:
        if not Quartz.CGEventTapIsEnabled(tap):
            log.warning("Event tap disabled by macOS, re-enabling...")
            Quartz.CGEventTapEnable(tap, True)

    def run(self) -> None:
        validate_setup()

        mask = CGEventMaskBit(kCGEventKeyDown) | CGEventMaskBit(kCGEventKeyUp)
        tap = CGEventTapCreate(
            kCGSessionEventTap,
            kCGHeadInsertEventTap,
            0,
            mask,
            self._event_callback,
            None,
        )
        if tap is None:
            log.error("Failed to create event tap — grant Accessibility in System Settings")
            raise SystemExit(1)

        source = CFMachPortCreateRunLoopSource(None, tap, 0)
        CFRunLoopAddSource(CFRunLoopGetCurrent(), source, kCFRunLoopCommonModes)
        Quartz.CGEventTapEnable(tap, True)

        watchdog_cb = lambda t, i: self._watchdog(tap, t, i)
        timer = Quartz.CFRunLoopTimerCreate(None, 0, 5.0, 0, 0, watchdog_cb, None)
        CFRunLoopAddSource(CFRunLoopGetCurrent(), timer, kCFRunLoopCommonModes)

        log.info("Push-to-Talk ready — F5=Russian, F6=English")
        CFRunLoopRun()


if __name__ == "__main__":
    PushToTalkApp().run()
