#!/usr/bin/env python3
"""macOS Push-to-Talk dictation daemon using whisper.cpp, triggered by holding F5."""

import logging
import os
import re
import shutil
import subprocess
import tempfile
import threading
import time

import Quartz
from Quartz import (
    CGEventGetIntegerValueField,
    CGEventMaskBit,
    CGEventTapCreate,
    CFMachPortCreateRunLoopSource,
    CFRunLoopAddSource,
    CFRunLoopGetCurrent,
    CFRunLoopRun,
    kCGEventKeyDown,
    kCGEventKeyUp,
    kCGEventFlagMaskSecondaryFn,
    kCGHeadInsertEventTap,
    kCGKeyboardEventKeycode,
    kCGSessionEventTap,
    kCFRunLoopCommonModes,
)

WHISPER_CLI = shutil.which("whisper-cli") or "/opt/homebrew/bin/whisper-cli"
MODEL_PATH = os.path.expanduser("~/.local/share/whisper-cpp/ggml-large-v3-turbo.bin")
RECORD_PATH = os.path.join(tempfile.gettempdir(), "dictation.wav")
MIN_DURATION_S = 0.4
F5_KEYCODE = 0x60  # Russian
F6_KEYCODE = 0x61  # English
HOTKEY_LANG = {F5_KEYCODE: "ru", F6_KEYCODE: "en"}

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("pushtotalk")


def notify(title: str, msg: str = ""):
    pass


def validate_setup():
    if not os.path.isfile(WHISPER_CLI):
        log.error("whisper-cli not found at %s — install via: brew install whisper-cpp", WHISPER_CLI)
        raise SystemExit(1)
    if not os.path.isfile(MODEL_PATH):
        log.error("Model not found at %s — download from https://huggingface.co/ggerganov/whisper.cpp", MODEL_PATH)
        raise SystemExit(1)


class Recorder:
    def __init__(self):
        self._proc = None
        self._start_time = 0.0

    def start(self):
        self._start_time = time.monotonic()
        self._proc = subprocess.Popen([
            "ffmpeg", "-y", "-f", "avfoundation", "-i", ":default",
            "-ar", "16000", "-ac", "1", "-sample_fmt", "s16",
            RECORD_PATH,
        ], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        log.info("Recording started")
        notify("🎙️ Recording...")

    def stop(self) -> str | None:
        if not self._proc:
            return None
        self._proc.terminate()
        self._proc.wait()
        self._proc = None
        duration = time.monotonic() - self._start_time
        if duration < MIN_DURATION_S:
            log.info("Recording too short (%.2fs), ignoring", duration)
            return None
        log.info("Recording stopped (%.2fs)", duration)
        return RECORD_PATH


def transcribe(wav_path: str, lang: str = "auto") -> str | None:
    log.info("Transcribing (%s)...", lang)
    result = subprocess.run(
        [WHISPER_CLI, "-m", MODEL_PATH, "-f", wav_path, "--no-timestamps", "-t", "4", "-l", lang],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        log.error("whisper-cli failed: %s", result.stderr.strip())
        return None
    text = result.stdout.strip()
    text = re.sub(r"\[.*?\]", "", text).strip()
    text = re.sub(r"^[-\s]+", "", text).strip()
    if not text:
        log.info("Hallucination or silence detected, ignoring")
        return None
    low = text.lower().strip(" .!-")
    if low in ("you", "thank you", "thanks for watching", "silence"):
        log.info("Hallucination or silence detected, ignoring")
        return None
    return text


def paste_text(text: str):
    subprocess.run(["pbcopy"], input=text.encode(), check=True)
    time.sleep(0.05)
    src = Quartz.CGEventSourceCreate(Quartz.kCGEventSourceStateCombinedSessionState)
    down = Quartz.CGEventCreateKeyboardEvent(src, 0x09, True)
    up = Quartz.CGEventCreateKeyboardEvent(src, 0x09, False)
    Quartz.CGEventSetFlags(down, Quartz.kCGEventFlagMaskCommand)
    Quartz.CGEventSetFlags(up, Quartz.kCGEventFlagMaskCommand)
    Quartz.CGEventPost(Quartz.kCGAnnotatedSessionEventTap, down)
    Quartz.CGEventPost(Quartz.kCGAnnotatedSessionEventTap, up)
    log.info("Pasted: %s", text)



def main():
    validate_setup()
    recorder = Recorder()
    lock = threading.Lock()
    recording = False
    active_lang = "auto"

    def handle_release(lang):
        wav = recorder.stop()
        if wav:
            text = transcribe(wav, lang)
            if text:
                paste_text(text)

    def event_callback(proxy, event_type, event, refcon):
        nonlocal recording, active_lang
        keycode = CGEventGetIntegerValueField(event, kCGKeyboardEventKeycode)
        if keycode not in HOTKEY_LANG:
            return event

        if event_type == kCGEventKeyDown:
            with lock:
                if recording:
                    return None
                recording = True
                active_lang = HOTKEY_LANG[keycode]
            recorder.start()
            return None

        if event_type == kCGEventKeyUp:
            with lock:
                if not recording:
                    return None
                recording = False
                lang = active_lang
            threading.Thread(target=handle_release, args=(lang,), daemon=True).start()
            return None

        return event

    mask = CGEventMaskBit(kCGEventKeyDown) | CGEventMaskBit(kCGEventKeyUp)
    tap = CGEventTapCreate(
        kCGSessionEventTap,
        kCGHeadInsertEventTap,
        0,  # active filter (not listen-only)
        mask,
        event_callback,
        None,
    )
    if tap is None:
        log.error("Failed to create event tap — grant Accessibility permission in System Settings")
        raise SystemExit(1)

    source = CFMachPortCreateRunLoopSource(None, tap, 0)
    CFRunLoopAddSource(CFRunLoopGetCurrent(), source, kCFRunLoopCommonModes)
    Quartz.CGEventTapEnable(tap, True)

    log.info("Push-to-Talk ready — F5=Russian, F6=English")

    CFRunLoopRun()


if __name__ == "__main__":
    main()
