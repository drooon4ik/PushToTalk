<#
.SYNOPSIS
    One-shot installer for Push-to-Talk on Windows.

.DESCRIPTION
    Installs everything needed to run push_to_talk.py on Windows:
      1. Python 3 (via winget if available, otherwise the official installer).
      2. Python dependencies from requirements.txt (pyaudio, pynput).
      3. whisper.cpp (whisper-cli.exe + DLLs) into %LOCALAPPDATA%\whisper-cpp.
      4. A Whisper model into %LOCALAPPDATA%\whisper-cpp.
      5. (Optional) Auto-start at Windows logon via a hidden pythonw task.

    Re-running the script is safe: existing, valid components are skipped.

.PARAMETER Model
    Which Whisper model to download. One of:
      large-v3-turbo (default, ~1.5 GB, best quality)
      medium         (~1.5 GB)
      small          (~0.5 GB)
      base           (~150 MB, fastest, lower quality)
      tiny           (~75 MB)
    Pass 'none' to skip the model download.

.PARAMETER AutoStart
    Register the app to start automatically at Windows logon (hidden window).

.PARAMETER NoWhisper
    Skip downloading the whisper.cpp binaries.

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File install_windows.ps1

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File install_windows.ps1 -Model small -AutoStart
#>
[CmdletBinding()]
param(
    [ValidateSet('large-v3-turbo', 'medium', 'small', 'base', 'tiny', 'none')]
    [string]$Model = 'large-v3-turbo',
    [switch]$AutoStart,
    [switch]$NoWhisper
)

$ErrorActionPreference = 'Stop'
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

$ScriptDir  = $PSScriptRoot
$ScriptPath = Join-Path $ScriptDir 'push_to_talk.py'
$WhisperDir = Join-Path $env:LOCALAPPDATA 'whisper-cpp'
$ModelFile  = Join-Path $WhisperDir 'ggml-large-v3-turbo.bin'   # script expects this name

function Write-Step($msg) { Write-Host "`n==> $msg" -ForegroundColor Cyan }
function Write-Ok($msg)   { Write-Host "    [OK] $msg" -ForegroundColor Green }
function Write-Warn2($msg) { Write-Host "    [!]  $msg" -ForegroundColor Yellow }

# Run an external command without letting its stderr output abort the script
# (native tools like pip print warnings to stderr; with ErrorActionPreference
# = 'Stop' that would otherwise be treated as a terminating error). Returns the
# process exit code and streams output to the console.
function Invoke-Native {
    param([Parameter(Mandatory)][string]$Exe, [string[]]$Arguments = @())
    $prev = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        & $Exe @Arguments 2>&1 | ForEach-Object { Write-Host $_ }
        return $LASTEXITCODE
    } finally {
        $ErrorActionPreference = $prev
    }
}

# ---------------------------------------------------------------------------
# 0. Sanity check
# ---------------------------------------------------------------------------
if (-not (Test-Path $ScriptPath)) {
    throw "push_to_talk.py not found next to this installer ($ScriptPath). Run the installer from inside the project folder."
}

# ---------------------------------------------------------------------------
# 1. Python
# ---------------------------------------------------------------------------
Write-Step 'Checking for Python'

function Get-PythonExe {
    foreach ($cmd in @('py', 'python')) {
        $c = Get-Command $cmd -ErrorAction SilentlyContinue
        if ($c) {
            try {
                $v = & $c.Source --version 2>&1
                if ($v -match 'Python 3\.(\d+)' -and [int]$Matches[1] -ge 9) {
                    return $c.Source
                }
            } catch { }
        }
    }
    return $null
}

$python = Get-PythonExe
if ($python) {
    Write-Ok "Python found: $python ($(& $python --version 2>&1))"
} else {
    Write-Warn2 'Python 3.9+ not found. Installing...'
    $winget = Get-Command winget -ErrorAction SilentlyContinue
    if ($winget) {
        Write-Host '    Installing via winget...'
        Invoke-Native 'winget' @('install', '-e', '--id', 'Python.Python.3.11', '--accept-source-agreements', '--accept-package-agreements', '--silent') | Out-Null
    } else {
        Write-Host '    winget not available, downloading official installer...'
        $installer = Join-Path $env:TEMP 'python-ptt-installer.exe'
        Invoke-WebRequest -Uri 'https://www.python.org/ftp/python/3.11.9/python-3.11.9-amd64.exe' -OutFile $installer -UseBasicParsing
        $args = @('/quiet', 'InstallAllUsers=0', 'PrependPath=1', 'Include_pip=1', 'Include_launcher=1')
        $p = Start-Process -FilePath $installer -ArgumentList $args -Wait -PassThru
        if ($p.ExitCode -ne 0) { throw "Python installer failed with exit code $($p.ExitCode)" }
        Remove-Item $installer -Force -ErrorAction SilentlyContinue
    }
    # Refresh PATH for the current session
    $env:Path = [Environment]::GetEnvironmentVariable('Path', 'Machine') + ';' + [Environment]::GetEnvironmentVariable('Path', 'User')
    $python = Get-PythonExe
    if (-not $python) {
        throw 'Python installed but not detected. Open a NEW terminal and re-run this installer.'
    }
    Write-Ok "Python installed: $python"
}

# Resolve the matching pythonw.exe (no console window) for autostart.
# $python may be the "py.exe" launcher, which has no sibling pythonw.exe,
# so ask Python itself where its real interpreter lives (sys.executable).
# Force UTF-8 so non-ASCII user paths (e.g. Cyrillic profile names) survive.
$pythonw = $null
try {
    $prevEnc = [Console]::OutputEncoding
    $env:PYTHONIOENCODING = 'utf-8'
    [Console]::OutputEncoding = [System.Text.Encoding]::UTF8
    try {
        $realPython = (& $python -c "import sys; sys.stdout.reconfigure(encoding='utf-8'); print(sys.executable)" 2>&1 | Select-Object -Last 1).Trim()
    } finally {
        [Console]::OutputEncoding = $prevEnc
    }
    if ($realPython -and (Test-Path -LiteralPath $realPython)) {
        $candidate = Join-Path (Split-Path $realPython -Parent) 'pythonw.exe'
        if (Test-Path -LiteralPath $candidate) { $pythonw = $candidate }
    }
} catch { }

# ---------------------------------------------------------------------------
# 2. Python dependencies
# ---------------------------------------------------------------------------
Write-Step 'Installing Python dependencies'
$req = Join-Path $ScriptDir 'requirements.txt'
Invoke-Native $python @('-m', 'pip', 'install', '--upgrade', 'pip') | Out-Null
$pipCode = Invoke-Native $python @('-m', 'pip', 'install', '-r', $req)
if ($pipCode -ne 0) {
    Write-Warn2 'pip failed on requirements.txt. Trying pyaudio via a prebuilt wheel fallback (pipwin)...'
    Invoke-Native $python @('-m', 'pip', 'install', 'pipwin') | Out-Null
    Invoke-Native $python @('-m', 'pipwin', 'install', 'pyaudio') | Out-Null
    Invoke-Native $python @('-m', 'pip', 'install', 'pynput') | Out-Null
}
Write-Ok 'Dependencies installed (pyaudio, pynput).'

# ---------------------------------------------------------------------------
# 3. whisper.cpp binaries
# ---------------------------------------------------------------------------
if ($NoWhisper) {
    Write-Step 'Skipping whisper.cpp download (-NoWhisper)'
} else {
    Write-Step 'Installing whisper.cpp (GPU / CUDA build)'
    New-Item -ItemType Directory -Path $WhisperDir -Force | Out-Null
    $cliTarget = Join-Path $WhisperDir 'whisper-cli.exe'

    # This project uses the NVIDIA GPU (CUDA) build only.
    $hasNvidia = $false
    try {
        $gpus = Get-CimInstance Win32_VideoController -ErrorAction SilentlyContinue
        if ($gpus | Where-Object { $_.Name -match 'NVIDIA' }) { $hasNvidia = $true }
    } catch { }
    if (-not $hasNvidia) {
        Write-Warn2 'No NVIDIA GPU detected — the CUDA build may fail to run on this machine.'
    } else {
        Write-Ok 'NVIDIA GPU detected — installing the CUDA 12.4 build.'
    }

    # Pinned known-good nightly build that ships Windows binaries.
    $buildTag   = 'b5454'
    $baseUrl    = "https://github.com/ggml-org/whisper.cpp/releases/download/$buildTag"
    $cpuZipUrl  = "$baseUrl/whisper-bin-x64.zip"                   # base ggml DLLs
    $cudaZipUrl = "$baseUrl/whisper-bin-win-cuda-12.4.0-x64.zip"   # GPU build

    # Detect whether an existing install is already a CUDA build. The CUDA
    # package ships ggml-cuda / cudart DLLs; a CPU-only install does not.
    function Test-CudaInstalled {
        if (-not (Test-Path $cliTarget)) { return $false }
        $cuda = Get-ChildItem -Path $WhisperDir -Filter '*.dll' -ErrorAction SilentlyContinue |
                Where-Object { $_.Name -match 'cuda|cudart|cublas' }
        return [bool]$cuda
    }

    # Helper: download a zip and copy its exe/dll into $WhisperDir.
    function Expand-WhisperZip($url, $label) {
        $zip = Join-Path $env:TEMP ("whisper-" + [IO.Path]::GetFileName($url))
        $tmp = Join-Path $env:TEMP ("whisper-extract-" + [Guid]::NewGuid().ToString('N'))
        Write-Host "    Downloading $label`:`n      $url"
        Write-Host '    (this can be large and may take a while...)'
        try {
            Start-BitsTransfer -Source $url -Destination $zip -Description "whisper.cpp $label"
        } catch {
            Invoke-WebRequest -Uri $url -OutFile $zip -UseBasicParsing
        }
        if (Test-Path $tmp) { Remove-Item $tmp -Recurse -Force }
        Expand-Archive -Path $zip -DestinationPath $tmp -Force
        $cli = Get-ChildItem -Path $tmp -Recurse -Filter 'whisper-cli.exe' | Select-Object -First 1
        if ($cli) { $srcDir = $cli.Directory.FullName } else { $srcDir = $tmp }
        Copy-Item (Join-Path $srcDir '*.exe') $WhisperDir -Force -ErrorAction SilentlyContinue
        Copy-Item (Join-Path $srcDir '*.dll') $WhisperDir -Force -ErrorAction SilentlyContinue
        Remove-Item $zip -Force -ErrorAction SilentlyContinue
        Remove-Item $tmp -Recurse -Force -ErrorAction SilentlyContinue
    }

    if (Test-CudaInstalled) {
        Write-Ok "CUDA build already present: $cliTarget"
    } else {
        if (Test-Path $cliTarget) {
            # An older CPU-only build exists. Replace just the binaries/DLLs
            # with the GPU build; the model file is left untouched.
            Write-Warn2 'Existing CPU build found — replacing it with the GPU (CUDA) build.'
            Write-Warn2 'The downloaded model is kept (not re-downloaded).'
            Get-ChildItem -Path $WhisperDir -Include '*.exe', '*.dll' -File -ErrorAction SilentlyContinue |
                Remove-Item -Force -ErrorAction SilentlyContinue
        }

        # The CUDA package relies on base ggml DLLs shipped in the CPU zip, so
        # lay down the CPU base first, then overlay the CUDA build on top.
        Expand-WhisperZip $cpuZipUrl  'CPU base (DLLs)'
        Expand-WhisperZip $cudaZipUrl 'CUDA 12.4 GPU build'

        if (-not (Test-Path $cliTarget)) {
            throw "whisper-cli.exe not found after extraction in $WhisperDir."
        }
        if (Test-CudaInstalled) {
            Write-Ok "GPU (CUDA) whisper-cli.exe installed to $WhisperDir"
        } else {
            Write-Warn2 "Installed whisper-cli, but no CUDA DLLs detected in $WhisperDir."
        }
    }
}

# ---------------------------------------------------------------------------
# 4. Whisper model
# ---------------------------------------------------------------------------
if ($Model -eq 'none') {
    Write-Step 'Skipping model download (-Model none)'
} else {
    Write-Step "Downloading Whisper model: $Model"
    New-Item -ItemType Directory -Path $WhisperDir -Force | Out-Null

    # Map friendly name -> Hugging Face file. We always save to the name the
    # script expects (ggml-large-v3-turbo.bin) so no code change is needed.
    $modelFileMap = @{
        'large-v3-turbo' = 'ggml-large-v3-turbo.bin'
        'medium'         = 'ggml-medium.bin'
        'small'          = 'ggml-small.bin'
        'base'           = 'ggml-base.bin'
        'tiny'           = 'ggml-tiny.bin'
    }
    $srcName = $modelFileMap[$Model]
    $url = "https://huggingface.co/ggerganov/whisper.cpp/resolve/main/$srcName"

    if (Test-Path $ModelFile) {
        Write-Ok "Model already present: $ModelFile ($([math]::Round((Get-Item $ModelFile).Length/1MB)) MB)"
    } else {
        Write-Host "    Downloading $url"
        Write-Host '    (this is large and may take a while...)'
        # BITS gives a resumable, progress-aware transfer when available.
        try {
            Start-BitsTransfer -Source $url -Destination $ModelFile -Description "Whisper model $Model"
        } catch {
            Write-Warn2 'BITS unavailable, falling back to Invoke-WebRequest...'
            Invoke-WebRequest -Uri $url -OutFile $ModelFile -UseBasicParsing
        }
        Write-Ok "Model saved: $ModelFile ($([math]::Round((Get-Item $ModelFile).Length/1MB)) MB)"
    }
}

# ---------------------------------------------------------------------------
# 5. Auto-start at logon
# ---------------------------------------------------------------------------
if ($AutoStart) {
    Write-Step 'Configuring auto-start at Windows logon'
    $runExe = if ($pythonw) { $pythonw } else { $python }
    if (-not $pythonw) {
        Write-Warn2 'pythonw.exe not found; a console window will appear at logon.'
    }

    # Prefer a Scheduled Task (robust, hidden). Fall back to a Startup shortcut.
    $taskName = 'PushToTalkDictation'
    $registered = $false
    try {
        $action  = New-ScheduledTaskAction -Execute $runExe -Argument "`"$ScriptPath`"" -WorkingDirectory $ScriptDir
        $trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
        $settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable
        $principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive -RunLevel Limited
        Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger -Settings $settings -Principal $principal -Force | Out-Null
        $registered = $true
        Write-Ok "Scheduled task '$taskName' registered (runs at logon)."
    } catch {
        Write-Warn2 "Scheduled task failed ($($_.Exception.Message)); using a Startup shortcut instead."
    }

    if (-not $registered) {
        $startup = [Environment]::GetFolderPath('Startup')
        $lnk = Join-Path $startup 'PushToTalk.lnk'
        $wsh = New-Object -ComObject WScript.Shell
        $sc = $wsh.CreateShortcut($lnk)
        $sc.TargetPath = $runExe
        $sc.Arguments = "`"$ScriptPath`""
        $sc.WorkingDirectory = $ScriptDir
        $sc.WindowStyle = 7   # minimized
        $sc.Description = 'Push-to-Talk dictation'
        $sc.Save()
        Write-Ok "Startup shortcut created: $lnk"
    }
} else {
    Write-Step 'Auto-start not requested (pass -AutoStart to enable)'
}

# ---------------------------------------------------------------------------
# Done
# ---------------------------------------------------------------------------
Write-Step 'Installation complete'
Write-Host ''
Write-Host '  Start now (manual):' -ForegroundColor White
Write-Host "    `"$python`" `"$ScriptPath`"" -ForegroundColor Gray
if ($pythonw) {
    Write-Host '  Start hidden (no console window):' -ForegroundColor White
    Write-Host "    Start-Process `"$pythonw`" -ArgumentList '`"$ScriptPath`"' -WindowStyle Hidden" -ForegroundColor Gray
}
Write-Host ''
Write-Host '  Hold F5 = Russian, F6 = English. Release to paste.' -ForegroundColor White
if ($AutoStart) {
    Write-Host '  It will also start automatically next time you log in.' -ForegroundColor White
    Write-Host "  To disable: powershell -File uninstall_autostart.ps1" -ForegroundColor Gray
}
Write-Host ''
