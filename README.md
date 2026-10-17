# Push-to-Talk Dictation (macOS & Windows)

Local speech-to-text using [whisper.cpp](https://github.com/ggerganov/whisper.cpp).
Hold **F5** for Russian, **F6** for English — release to paste the transcribed text at the cursor.

Everything runs locally: audio never leaves your machine.

- **macOS** — hotkeys & paste via Quartz, clipboard via `pbcopy`, sound via `afplay`.
- **Windows** — hotkeys & paste via `pynput`, clipboard via the Win32 API, sound via `winsound`.

The script auto-detects your OS and picks the right backend.

---

## macOS

### 1. Install dependencies

```bash
brew install whisper-cpp ffmpeg
pip3 install -r requirements.txt
```

### 2. Download the Whisper model (~1.5 GB)

```bash
mkdir -p ~/.local/share/whisper-cpp
curl -L "https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-large-v3-turbo.bin" \
  -o ~/.local/share/whisper-cpp/ggml-large-v3-turbo.bin
```

### 3. Grant macOS permissions

Go to **System Settings → Privacy & Security** and add your terminal (or iTerm) and Python.app to:

- **Accessibility** — required for global key listening and simulated paste
  - Add `/Library/Frameworks/Python.framework/Versions/3.11/Resources/Python.app`
- **Microphone** — required for audio recording
- **Input Monitoring** — required for intercepting F5/F6 keys

> macOS will prompt on first run if permissions are missing.

### 4. Run

```bash
python3 push_to_talk.py
```

> Press F5/F6 in any app **except** the terminal running the script.

#### Auto-start on iTerm launch

Add to your `~/.zshrc`:

```bash
# Push-to-Talk: start in background if not already running
_ptt_pid="/tmp/pushtotalk.pid"
if [ -f "$_ptt_pid" ] && kill -0 "$(cat "$_ptt_pid")" 2>/dev/null; then
    echo "🎙️ PushToTalk already running (pid $(cat "$_ptt_pid"))"
else
    nohup /usr/local/bin/python3 /path/to/PushToTalk/push_to_talk.py >> /tmp/pushtotalk.log 2>&1 &
    echo $! > "$_ptt_pid"
    echo "🎙️ PushToTalk started (pid $!)"
fi
```

Stop: `pkill -9 -f push_to_talk.py; rm -f /tmp/pushtotalk.pid`
Check: `pgrep -f push_to_talk.py && echo "✅ Running" || echo "❌ Not running"`
Logs: `tail -f /tmp/pushtotalk.log`

---

## Windows

### Quick install (recommended)

An all-in-one installer sets up **everything** — Python, dependencies, whisper.cpp,
the model, and auto-start at logon.

Open **PowerShell** in the project folder and run:

```powershell
powershell -ExecutionPolicy Bypass -File install_windows.ps1
```

By default the app is also registered to start automatically every time you log in
to Windows (hidden, no console window). To skip that, add `-NoAutoStart`:

```powershell
powershell -ExecutionPolicy Bypass -File install_windows.ps1 -NoAutoStart
```

Options:

| Flag | Meaning |
| --- | --- |
| `-Model <name>` | Model to download: `large-v3-turbo` (default, ~1.5 GB), `medium`, `small`, `base`, `tiny`, or `none` to skip. |
| `-NoAutoStart` | Do **not** register the logon task (auto-start is on by default). |
| `-NoWhisper` | Skip downloading the whisper.cpp binaries. |

Examples:

```powershell
# Smaller/faster model (auto-start still enabled by default):
powershell -ExecutionPolicy Bypass -File install_windows.ps1 -Model small

# Everything except the model (download it yourself later):
powershell -ExecutionPolicy Bypass -File install_windows.ps1 -Model none

# Install without registering auto-start:
powershell -ExecutionPolicy Bypass -File install_windows.ps1 -NoAutoStart
```

What the installer does:

1. Installs Python 3.11 (via `winget`, or the official installer) if missing.
2. Installs `pyaudio` and `pynput` from `requirements.txt`.
3. Downloads whisper.cpp (`whisper-cli.exe` + DLLs) to `%LOCALAPPDATA%\whisper-cpp`.
4. Downloads the Whisper model to `%LOCALAPPDATA%\whisper-cpp\ggml-large-v3-turbo.bin`.
5. Registers a hidden logon task named `PushToTalkDictation` (unless `-NoAutoStart`).

Re-running the installer is safe — it skips anything already in place.

**To start right away** after install:

```powershell
python push_to_talk.py
```

**To disable auto-start** later:

```powershell
powershell -ExecutionPolicy Bypass -File uninstall_autostart.ps1
```

---

### Manual install

If you'd rather set things up by hand, follow the steps below.

#### 1. Install Python

Install Python 3.9+ from [python.org](https://www.python.org/downloads/windows/) (tick
**"Add python.exe to PATH"** during setup) or from the Microsoft Store.

Verify in a new terminal:

```powershell
python --version
```

#### 2. Install dependencies

```powershell
pip install -r requirements.txt
```

This installs `pyaudio` and `pynput`. If `pip install pyaudio` fails to build, install a
prebuilt wheel instead:

```powershell
pip install pipwin
pipwin install pyaudio
```

(Alternatively grab a wheel from https://www.lfd.uci.edu/~gohlke/pythonlibs/ and
`pip install <file>.whl`.)

#### 3. Install whisper.cpp

Download a prebuilt release from
[whisper.cpp releases](https://github.com/ggml-org/whisper.cpp/releases) (the Windows
x64 asset is `whisper-bin-x64.zip`), or build it yourself. You need `whisper-cli.exe`.

The script finds the binary in this order:

1. The `WHISPER_CLI` environment variable (full path to the exe).
2. `whisper-cli.exe` / `whisper-cpp.exe` on your `PATH`.
3. Common locations, including `%LOCALAPPDATA%\whisper-cpp\whisper-cli.exe`.

The simplest setup is to put the exe (and its DLLs) here:

```powershell
mkdir "$env:LOCALAPPDATA\whisper-cpp"
# copy whisper-cli.exe and the accompanying *.dll into that folder
```

Or point at it explicitly (per session):

```powershell
$env:WHISPER_CLI = "C:\path\to\whisper-cli.exe"
```

To set it permanently: **System → Advanced system settings → Environment Variables**.

#### 4. Download the Whisper model (~1.5 GB)

```powershell
mkdir "$env:LOCALAPPDATA\whisper-cpp"
curl.exe -L "https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-large-v3-turbo.bin" `
  -o "$env:LOCALAPPDATA\whisper-cpp\ggml-large-v3-turbo.bin"
```

The script expects the model at `%LOCALAPPDATA%\whisper-cpp\ggml-large-v3-turbo.bin`.

#### 5. Run

```powershell
python push_to_talk.py
```

- Hold **F5** → speak Russian → release → text is pasted at the cursor.
- Hold **F6** → speak English → release → text is pasted at the cursor.

> Windows does **not** require special accessibility permissions for global hotkeys.
> If your antivirus flags the key listener, allow `python.exe`.

#### Run in the background

Run without a console window using `pythonw.exe`:

```powershell
Start-Process pythonw -ArgumentList "$PWD\push_to_talk.py" -WindowStyle Hidden
```

Stop it from Task Manager (end the `pythonw.exe` process) or:

```powershell
Get-Process pythonw -ErrorAction SilentlyContinue | Stop-Process
```

#### Auto-start at logon (manual)

The quick installer handles this by default (disable with `-NoAutoStart`). To do
it by hand, register a
Scheduled Task that runs at logon with no console window:

```powershell
$script = "$PWD\push_to_talk.py"
$pythonw = (Get-Command pythonw).Source
$action  = New-ScheduledTaskAction -Execute $pythonw -Argument "`"$script`"" -WorkingDirectory $PWD
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
Register-ScheduledTask -TaskName 'PushToTalkDictation' -Action $action -Trigger $trigger -Force
```

Disable it again:

```powershell
Unregister-ScheduledTask -TaskName 'PushToTalkDictation' -Confirm:$false
# or simply:  powershell -ExecutionPolicy Bypass -File uninstall_autostart.ps1
```

#### Regenerating the cue sounds (optional)

The Windows build ships with `sounds/begin_record.wav` and `sounds/end_record.wav`.
To regenerate them:

```powershell
powershell -ExecutionPolicy Bypass -File make_sounds.ps1
```

---

## How it works

```
F5/F6 pressed ──▶ play "begin" sound ──▶ start recording (PyAudio, 16 kHz mono)
F5/F6 released ─▶ play "end" sound  ──▶ stop recording ──▶ whisper-cli transcribes
                                                        └▶ clean hallucinations
                                                        └▶ copy to clipboard + paste
```

- Recordings shorter than 0.4 s are ignored.
- Common whisper hallucinations (e.g. "you", "thanks for watching", "продолжение следует")
  are filtered out.
- Language is forced per hotkey (F5 = `ru`, F6 = `en`).

## Troubleshooting

| Problem | Fix |
| --- | --- |
| `whisper-cli not found` | Set `WHISPER_CLI` or place the exe/binary on `PATH`. |
| `Model not found` | Download the model to the path shown in the error. |
| No audio recorded | Check microphone permissions / default input device. |
| `pyaudio` won't install (Windows) | Use `pipwin install pyaudio` or a prebuilt wheel. |
| Paste does nothing | Make sure the target window has focus; some apps block simulated input. |

## Requirements

- Python 3.9+
- whisper.cpp (`whisper-cli`)
- A microphone
