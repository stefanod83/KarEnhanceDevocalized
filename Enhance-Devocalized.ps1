param(
    [Parameter(Mandatory=$true)]
    [ValidateScript({ Test-Path $_ })]
    [string]$InputFile,

    [Parameter(Mandatory=$true)]
    [ValidateRange(0,5)]
    [int]$EqLevel,

    [Parameter(Mandatory=$true)]
    [ValidateRange(0,5)]
    [int]$CompLevel,

    [Parameter(Mandatory=$false)]
    [string]$RangeSeconds,

    [Parameter(Mandatory=$false)]
    [string]$VocalFile,

    [Parameter(Mandatory=$false)]
    [ValidateRange(1,5)]
    [int]$Sensitivity = 3,

    [Parameter(Mandatory=$false)]
    [double]$MinGap = 1.5,

    [Parameter(Mandatory=$false)]
    [double]$MinDuration = 0.5,

    [Parameter(Mandatory=$false)]
    [ValidateSet("none","peak","loudness")]
    [string]$Normalization = "none",

    [switch]$StereoWiden,
    [switch]$WhatIf
)

# --- Validazione ---
if ($RangeSeconds -and $VocalFile) {
    Write-Error "Specifica -RangeSeconds OPPURE -VocalFile, non entrambi."
    exit 1
}
if ($VocalFile -and !(Test-Path $VocalFile)) {
    Write-Error "File vocale non trovato: $VocalFile"
    exit 1
}

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$RANGE_PATTERN = '^(\d+(\.\d+)?)-(\d+(\.\d+)?)$'

$codecMap = @{
    '.mp3'  = @{ codec = $null }
    '.flac' = @{ codec = 'flac' }
    '.wav'  = @{ codec = 'pcm_s16le' }
    '.opus' = @{ codec = 'libopus' }
    '.ogg'  = @{ codec = 'libvorbis' }
    '.m4a'  = @{ codec = 'aac' }
    '.aac'  = @{ codec = 'aac' }
}

# --- Risoluzione path ---
$InputFile = Resolve-Path $InputFile
$directory = [IO.Path]::GetDirectoryName($InputFile)
$filename  = [IO.Path]::GetFileNameWithoutExtension($InputFile)
$extension = [IO.Path]::GetExtension($InputFile).ToLower()
$output    = Join-Path $directory ($filename + "_EnDe" + $extension)

# === FUNZIONI ===

function Get-AudioBitrate([string]$file) {
    $oldEAP = $ErrorActionPreference
    $ErrorActionPreference = 'SilentlyContinue'
    $probe = & ffprobe -v error -select_streams a:0 `
        -show_entries stream=bit_rate `
        -of default=noprint_wrappers=1:nokey=1 "$file" 2>&1
    $ErrorActionPreference = $oldEAP
    $val = 0
    if ([int]::TryParse($probe, [ref]$val) -and $val -gt 0) { return $val }
    return 192000
}

function Get-AudioDuration([string]$file) {
    $oldEAP = $ErrorActionPreference
    $ErrorActionPreference = 'SilentlyContinue'
    $probe = & ffprobe -v error -show_entries format=duration `
        -of default=noprint_wrappers=1:nokey=1 "$file" 2>&1
    $ErrorActionPreference = $oldEAP
    $val = 0.0
    $probeStr = "$probe".Trim()
    if ([double]::TryParse($probeStr, [System.Globalization.NumberStyles]::Float, [System.Globalization.CultureInfo]::InvariantCulture, [ref]$val) -and $val -gt 0) {
        return $val
    }
    Write-Error "Impossibile determinare la durata di: $file"
    exit 1
}

function Build-EffectChain([int]$eq, [int]$comp) {
    $eqFilters = @(
        "equalizer=f=120:t=q:w=0.8:g=$([Math]::Round($eq * 0.08, 2))"
        "equalizer=f=250:t=q:w=1.0:g=$([Math]::Round($eq * 0.10, 2))"
        "equalizer=f=2000:t=q:w=1.2:g=$([Math]::Round($eq * 0.15, 2))"
        "equalizer=f=3500:t=q:w=1.0:g=$([Math]::Round($eq * 0.10, 2))"
        "equalizer=f=8000:t=q:w=0.9:g=$([Math]::Round($eq * 0.06, 2))"
    )

    $framelen  = [Math]::Max(100, 500 - ($comp * 60))
    $gausssize = [Math]::Max(3, 31 - ($comp * 4))
    if ($gausssize % 2 -eq 0) { $gausssize++ }
    $maxgain   = [Math]::Round(3 + ($comp * 2.5), 1)
    $peak      = 0.95
    $compress  = [Math]::Round([Math]::Max(1, 10 - ($comp * 1.5)), 1)

    $dynFilter = "dynaudnorm=f=$framelen`:g=$gausssize`:p=$peak`:m=$maxgain`:s=$compress"
    $limiter   = "alimiter=limit=0.95:attack=5:release=50:level=disabled"

    $chain = ($eqFilters + $dynFilter + $limiter) -join ','
    return $chain
}

# --- Analisi traccia vocale con silencedetect ---
# Singola passata ffmpeg: silencedetect trova le zone silenziose.
# Le zone NON silenziose = dove c'era voce = dove applicare i filtri.
# Sensitivity controlla la soglia: 1=solo voce forte, 5=anche voce leggera.
function Analyze-VocalTrack([string]$vocalFile, [int]$sensitivity, [double]$minGap, [double]$minDuration) {
    Write-Host ""
    Write-Host "--- Analisi traccia vocale ---" -ForegroundColor Cyan

    $duration = Get-AudioDuration $vocalFile

    # Soglia silencedetect: sensitivity 1 = -20dB (solo voce forte)
    #                       sensitivity 5 = -45dB (anche voce leggera)
    $silenceThresholdDb = -20 - (($sensitivity - 1) * 6.25)
    # Durata minima silenzio: piu bassa = piu preciso nel trovare pause brevi
    $silenceMinDur = 0.3

    Write-Host ("  Soglia silenzio: {0}dB (sensitivity {1})" -f $silenceThresholdDb, $sensitivity)
    Write-Host "  Analisi in corso (singola passata)..." -ForegroundColor Yellow

    # Esegui silencedetect via bat per catturare stderr correttamente
    $stderrFile = Join-Path $env:TEMP "enhance_devoc_silence.txt"
    $batFile    = Join-Path $env:TEMP "enhance_devoc_cmd.bat"

    $batContent = 'ffmpeg -nostats -v info -i "{0}" -af "silencedetect=n={1}dB:d={2}" -f null NUL 2>"{3}"' -f `
        $vocalFile, $silenceThresholdDb, $silenceMinDur, $stderrFile

    Set-Content -Path $batFile -Value $batContent -Encoding ASCII
    & cmd /c $batFile 2>$null
    Remove-Item $batFile -Force -ErrorAction SilentlyContinue

    # Parse silence_start e silence_end
    $silenceRanges = [System.Collections.Generic.List[PSCustomObject]]::new()

    if (Test-Path $stderrFile) {
        $rawLines = @(Get-Content $stderrFile -ErrorAction SilentlyContinue)
        Remove-Item $stderrFile -Force -ErrorAction SilentlyContinue

        $curSilenceStart = $null
        foreach ($line in $rawLines) {
            if ($line -match 'silence_start:\s*([\d.]+)') {
                $curSilenceStart = [double]$Matches[1]
            }
            elseif ($line -match 'silence_end:\s*([\d.]+)' -and $null -ne $curSilenceStart) {
                $silenceRanges.Add([PSCustomObject]@{
                    Start = $curSilenceStart
                    End   = [double]$Matches[1]
                })
                $curSilenceStart = $null
            }
        }
        # Se c'e un silence_start senza end, il silenzio va fino alla fine
        if ($null -ne $curSilenceStart) {
            $silenceRanges.Add([PSCustomObject]@{
                Start = $curSilenceStart
                End   = $duration
            })
        }
    }

    if ($silenceRanges.Count -eq 0) {
        Write-Host "  Nessun silenzio rilevato - la voce e presente ovunque." -ForegroundColor Yellow
        # Tutto il file e voce: ritorna un singolo range
        return ,@([PSCustomObject]@{ Start = 0.0; End = [Math]::Round($duration, 2) })
    }

    Write-Host ("  Trovate {0} zone di silenzio" -f $silenceRanges.Count)

    # Inverti: le zone NON silenziose sono dove c'e voce
    $voiceRanges = [System.Collections.Generic.List[PSCustomObject]]::new()
    $pos = 0.0

    foreach ($s in $silenceRanges) {
        if ($s.Start -gt $pos) {
            $voiceRanges.Add([PSCustomObject]@{
                Start = [Math]::Round($pos, 2)
                End   = [Math]::Round($s.Start, 2)
            })
        }
        $pos = $s.End
    }
    # Coda dopo l'ultimo silenzio
    if ($pos -lt $duration) {
        $voiceRanges.Add([PSCustomObject]@{
            Start = [Math]::Round($pos, 2)
            End   = [Math]::Round($duration, 2)
        })
    }

    # Fondi range vicini (gap < MinGap)
    $merged = [System.Collections.Generic.List[PSCustomObject]]::new()
    if ($voiceRanges.Count -gt 0) {
        $curStart = $voiceRanges[0].Start
        $curEnd   = $voiceRanges[0].End

        for ($i = 1; $i -lt $voiceRanges.Count; $i++) {
            $v = $voiceRanges[$i]
            if (($v.Start - $curEnd) -le $minGap) {
                $curEnd = $v.End
            }
            else {
                if (($curEnd - $curStart) -ge $minDuration) {
                    $merged.Add([PSCustomObject]@{
                        Start = [Math]::Round($curStart, 2)
                        End   = [Math]::Round($curEnd, 2)
                    })
                }
                $curStart = $v.Start
                $curEnd   = $v.End
            }
        }
        if (($curEnd - $curStart) -ge $minDuration) {
            $merged.Add([PSCustomObject]@{
                Start = [Math]::Round($curStart, 2)
                End   = [Math]::Round($curEnd, 2)
            })
        }
    }

    $result = @($merged.ToArray())

    # Report
    if ($result.Count -gt 0) {
        $totalDur   = ($result | ForEach-Object { $_.End - $_.Start } | Measure-Object -Sum).Sum
        $pctCovered = [Math]::Round(($totalDur / $duration) * 100, 1)

        Write-Host ""
        Write-Host ("  Range vocali: {0}  (coprono {1}% della traccia)" -f $result.Count, $pctCovered) -ForegroundColor Green
        foreach ($r in $result) {
            $d = [Math]::Round($r.End - $r.Start, 1)
            Write-Host ("    {0}s - {1}s  [{2}s]" -f $r.Start, $r.End, $d) -ForegroundColor DarkCyan
        }
    }
    else {
        Write-Host "  Nessun range vocale significativo trovato." -ForegroundColor Yellow
    }

    return ,$result
}

# --- Parse range manuali ---
function Parse-Ranges([string]$raw) {
    $ranges = @()
    foreach ($part in ($raw -split ',')) {
        $part = $part.Trim()
        if ($part -notmatch $RANGE_PATTERN) {
            Write-Error "Formato range non valido: '$part'. Usa es: 30-60 oppure 75.5-102.2"
            exit 1
        }
        $s = [double]$Matches[1]
        $e = [double]$Matches[3]
        if ($e -le $s) {
            Write-Error "Fine range ($e) deve essere > inizio ($s) nel segmento '$part'"
            exit 1
        }
        $ranges += [PSCustomObject]@{ Start = $s; End = $e }
    }

    $ranges = @($ranges | Sort-Object Start)
    for ($i = 1; $i -lt $ranges.Count; $i++) {
        if ($ranges[$i].Start -lt $ranges[$i-1].End) {
            Write-Error ("I range si sovrappongono: {0}-{1} e {2}-{3}" -f `
                $ranges[$i-1].Start, $ranges[$i-1].End, $ranges[$i].Start, $ranges[$i].End)
            exit 1
        }
    }
    return ,$ranges
}

# --- Costruisce il filter_complex per range multipli ---
function Build-RangeFilter([array]$ranges, [string]$effectChain) {
    $segments = @()
    $labels   = @()
    $idx      = 0
    $prevEnd  = 0

    foreach ($r in $ranges) {
        if ($r.Start -gt $prevEnd) {
            $label = "a$idx"
            $segments += "[0:a]atrim=$prevEnd`:$($r.Start),asetpts=PTS-STARTPTS[$label]"
            $labels   += "[$label]"
            $idx++
        }

        $label = "a$idx"
        $segments += "[0:a]atrim=$($r.Start)`:$($r.End),asetpts=PTS-STARTPTS,$effectChain[$label]"
        $labels   += "[$label]"
        $idx++

        $prevEnd = $r.End
    }

    $label = "a$idx"
    $segments += "[0:a]atrim=$prevEnd,asetpts=PTS-STARTPTS[$label]"
    $labels   += "[$label]"
    $idx++

    $n = $labels.Count
    $filter = ($segments -join ';') + ";$($labels -join '')concat=n=$n`:v=0:a=1[out]"
    return $filter
}

# --- Determina argomenti codec di output ---
function Get-OutputCodecArgs([string]$ext, [string]$inputFile) {
    if ($codecMap.ContainsKey($ext)) {
        $info = $codecMap[$ext]
        if ($null -eq $info.codec) {
            $br = Get-AudioBitrate $inputFile
            return @('-b:a', $br)
        }
        return @('-c:a', $info.codec)
    }
    return @()
}

# === MAIN ===

$effectChain = Build-EffectChain $EqLevel $CompLevel

if ($StereoWiden) {
    $effectChain += ",extrastereo=m=1.3"
}

switch ($Normalization) {
    'peak'     { $effectChain += ",dynaudnorm=p=1" }
    'loudness' { $effectChain += ",loudnorm=i=-16:lra=7:tp=-1" }
}

$codecArgs = Get-OutputCodecArgs $extension $InputFile

# --- Determina i range da processare ---
$ranges = $null

if ($VocalFile) {
    $VocalFile = Resolve-Path $VocalFile
    $ranges    = Analyze-VocalTrack $VocalFile $Sensitivity $MinGap $MinDuration

    if ($ranges.Count -eq 0) {
        Write-Host ""
        Write-Host "Nessun range rilevato - niente da processare." -ForegroundColor Yellow
        exit 0
    }
}
elseif ($RangeSeconds) {
    $ranges = Parse-Ranges $RangeSeconds
}

# --- Costruisci comando ---
if ($null -eq $ranges) {
    $ffmpegArgs = @(
        '-y', '-i', "`"$InputFile`""
        '-af', "`"$effectChain`""
    ) + $codecArgs + @("`"$output`"")
}
else {
    $filter = Build-RangeFilter $ranges $effectChain
    $ffmpegArgs = @(
        '-y', '-i', "`"$InputFile`""
        '-filter_complex', "`"$filter`""
        '-map', '[out]'
    ) + $codecArgs + @("`"$output`"")
}

$cmdLine = "ffmpeg $($ffmpegArgs -join ' ')"

# --- Riepilogo ed esecuzione ---
Write-Host ""
Write-Host "--- Parametri ---" -ForegroundColor Cyan
Write-Host "  EQ Level:       $EqLevel"
Write-Host "  Comp Level:     $CompLevel"
Write-Host "  Normalization:  $Normalization"
Write-Host "  Stereo Widen:   $StereoWiden"
if ($VocalFile) {
    Write-Host "  Vocal File:     $VocalFile"
    Write-Host "  Sensitivity:    $Sensitivity"
    Write-Host ("  Min Gap:        {0}s" -f $MinGap)
    Write-Host ("  Min Duration:   {0}s" -f $MinDuration)
}
if ($ranges) {
    $rangeStr = ($ranges | ForEach-Object { "$($_.Start)-$($_.End)" }) -join ', '
    Write-Host "  Ranges:         $rangeStr"
}
Write-Host "  Output:         $output"
Write-Host ""
Write-Host "--- Comando ---" -ForegroundColor Cyan
Write-Host "  $cmdLine"
Write-Host ""

if ($WhatIf) {
    Write-Host "[WhatIf] Nessuna esecuzione." -ForegroundColor Yellow
}
else {
    $oldEAP = $ErrorActionPreference
    $ErrorActionPreference = 'SilentlyContinue'
    Invoke-Expression $cmdLine
    $ErrorActionPreference = $oldEAP
    if ($LASTEXITCODE -ne 0) {
        Write-Error "ffmpeg ha restituito errore (exit code $LASTEXITCODE)"
        exit $LASTEXITCODE
    }
    Write-Host ""
    Write-Host "Completato: $output" -ForegroundColor Green
}
