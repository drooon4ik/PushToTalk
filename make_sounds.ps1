# Generates two short WAV cue sounds for the Windows backend.
# begin_record.wav : short rising two-tone beep
# end_record.wav   : short falling two-tone beep
param()

$ErrorActionPreference = 'Stop'
$soundsDir = Join-Path $PSScriptRoot 'sounds'
if (-not (Test-Path $soundsDir)) { New-Item -ItemType Directory -Path $soundsDir | Out-Null }

$sampleRate = 44100
$bitsPerSample = 16
$channels = 1

function Write-Wav {
    param(
        [string]$Path,
        [double[]]$Freqs,     # tone per segment, in Hz
        [double]$SegSeconds   # duration of each segment
    )

    $amplitude = 0.35 * [math]::Pow(2, $bitsPerSample - 1)
    $samplesPerSeg = [int]($sampleRate * $SegSeconds)
    $totalSamples = $samplesPerSeg * $Freqs.Count

    $ms = New-Object System.IO.MemoryStream
    $bw = New-Object System.IO.BinaryWriter($ms)

    $segIndex = 0
    foreach ($f in $Freqs) {
        for ($i = 0; $i -lt $samplesPerSeg; $i++) {
            $t = $i / $sampleRate
            # simple linear fade in/out per segment to avoid clicks
            $env = 1.0
            $fade = [int]($samplesPerSeg * 0.08)
            if ($i -lt $fade) { $env = $i / $fade }
            elseif ($i -gt ($samplesPerSeg - $fade)) { $env = ($samplesPerSeg - $i) / $fade }
            $sample = [int]($amplitude * $env * [math]::Sin(2 * [math]::PI * $f * $t))
            if ($sample -gt 32767) { $sample = 32767 }
            if ($sample -lt -32768) { $sample = -32768 }
            $bw.Write([int16]$sample)
        }
        $segIndex++
    }
    $bw.Flush()
    $pcm = $ms.ToArray()
    $bw.Dispose()
    $ms.Dispose()

    $dataSize = $pcm.Length
    $byteRate = $sampleRate * $channels * ($bitsPerSample / 8)
    $blockAlign = $channels * ($bitsPerSample / 8)

    $fs = [System.IO.File]::Open($Path, [System.IO.FileMode]::Create)
    $w = New-Object System.IO.BinaryWriter($fs)
    # RIFF header
    $w.Write([System.Text.Encoding]::ASCII.GetBytes('RIFF'))
    $w.Write([int32](36 + $dataSize))
    $w.Write([System.Text.Encoding]::ASCII.GetBytes('WAVE'))
    # fmt chunk
    $w.Write([System.Text.Encoding]::ASCII.GetBytes('fmt '))
    $w.Write([int32]16)
    $w.Write([int16]1)                 # PCM
    $w.Write([int16]$channels)
    $w.Write([int32]$sampleRate)
    $w.Write([int32]$byteRate)
    $w.Write([int16]$blockAlign)
    $w.Write([int16]$bitsPerSample)
    # data chunk
    $w.Write([System.Text.Encoding]::ASCII.GetBytes('data'))
    $w.Write([int32]$dataSize)
    $w.Write($pcm)
    $w.Flush()
    $w.Dispose()
    $fs.Dispose()
}

Write-Wav -Path (Join-Path $soundsDir 'begin_record.wav') -Freqs @(660, 990) -SegSeconds 0.07
Write-Wav -Path (Join-Path $soundsDir 'end_record.wav')   -Freqs @(990, 660) -SegSeconds 0.07

$log = Join-Path $env:USERPROFILE 'wavgen.txt'
"begin size: $((Get-Item (Join-Path $soundsDir 'begin_record.wav')).Length)" | Out-File $log -Encoding utf8
"end size: $((Get-Item (Join-Path $soundsDir 'end_record.wav')).Length)"    | Out-File $log -Append -Encoding utf8
