# Push-to-Talk Dictation for macOS

Local speech-to-text using whisper.cpp. Hold **F5** for Russian, **F6** for English — release to paste transcribed text at cursor.

## First-Time Setup

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

Go to **System Settings → Privacy & Security** and add your Terminal app (or iTerm) and Python.app to:

- **Accessibility** — required for global key listening and simulated paste
  - Add `/Library/Frameworks/Python.framework/Versions/3.11/Resources/Python.app`
- **Microphone** — required for audio recording (granted via iTerm on first use)
- **Input Monitoring** — required for intercepting F5/F6 keys

> macOS will prompt on first run if permissions are missing.

## Usage

### Manual run

```bash
python3 push_to_talk.py
```

- Hold **F5** → speak Russian → release → text is pasted at cursor
- Hold **F6** → speak English → release → text is pasted at cursor

> Press F5/F6 in any app **except** the terminal running the script.

### Auto-start on iTerm launch

Add the following to your `~/.zshrc`:

```bash
# Push-to-Talk: start in background if not already running
_ptt_pid="/tmp/pushtotalk.pid"
if [ -f "$_ptt_pid" ] && kill -0 "$(cat "$_ptt_pid")" 2>/dev/null; then
    echo "🎙️ PushToTalk already running (pid $(cat "$_ptt_pid"))"
else
    nohup /usr/local/bin/python3 /Users/apochynok/PycharmProjects/PushToTalk/push_to_talk.py >> /tmp/pushtotalk.log 2>&1 &
    echo $! > "$_ptt_pid"
    echo "🎙️ PushToTalk started (pid $!)"
fi
```

The script starts in the background when you open iTerm. Subsequent tabs/windows will detect the running process and skip re-launching.

### Stop the service

```bash
pkill -9 -f push_to_talk.py; rm -f /tmp/pushtotalk.pid
```

### Check if running

```bash
pgrep -f push_to_talk.py && echo "✅ Running" || echo "❌ Not running"
```

## Logs

```bash
tail -f /tmp/pushtotalk.log
```
