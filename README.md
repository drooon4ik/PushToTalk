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

Go to **System Settings → Privacy & Security** and add your Terminal app (or iTerm / PyCharm) to:

- **Accessibility** — required for global key listening and simulated paste
- **Microphone** — required for audio recording
- **Input Monitoring** — required for intercepting F5/F6 keys

> macOS will prompt on first run if permissions are missing.

## Usage

```bash
python3 push_to_talk.py
```

- Hold **F5** → speak Russian → release → text is pasted at cursor
- Hold **F6** → speak English → release → text is pasted at cursor

## Auto-Start on Login

```bash
cp com.user.pushtotalk.plist ~/Library/LaunchAgents/
launchctl load ~/Library/LaunchAgents/com.user.pushtotalk.plist
```

To stop:
```bash
launchctl unload ~/Library/LaunchAgents/com.user.pushtotalk.plist
```

After editing the script, reload the service:
```bash
launchctl unload ~/Library/LaunchAgents/com.user.pushtotalk.plist
launchctl load ~/Library/LaunchAgents/com.user.pushtotalk.plist
```

Or just log out and log back in.

## Logs

```bash
tail -f /tmp/pushtotalk.log
```
