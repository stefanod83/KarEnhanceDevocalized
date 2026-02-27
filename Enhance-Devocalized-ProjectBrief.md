# Enhance-Devocalized.ps1 — Project Brief

## Scopo

Script PowerShell che compensa le tracce audio devocalizzate (karaoke) quando la rimozione della voce principale lascia "buchi" udibili nello strumentale. Funziona come wrapper attorno a ffmpeg.

## Contesto d'uso

L'utente lavora con tracce karaoke ottenute da tool come UVR (Ultimate Vocal Remover) o Demucs. La rimozione vocale a volte è troppo aggressiva e lascia cali di volume o buchi spettrali nelle sezioni dove la voce era prominente. Lo script applica filtri compensativi **solo dove servono**.

## Stato attuale del codice

Lo script è funzionante e si trova allegato a questo progetto. Ecco cosa fa:

### Tre modalità di funzionamento

1. **Tutto il file** — applica EQ + upward leveling a tutta la traccia
2. **Range manuali** (`-RangeSeconds "30-60,85-110"`) — applica solo ai range specificati
3. **Analisi automatica** (`-VocalFile vocals.wav`) — analizza la traccia vocale separata con `silencedetect` di ffmpeg, inverte i risultati (zone non-silenziose = dove c'era voce = dove servono i filtri), e applica solo lì

### Catena effetti

- **EQ a 5 bande** (120Hz sub-bass, 250Hz low-mid, 2kHz presence, 3.5kHz upper-mid, 8kHz air) — riempie le frequenze danneggiate dal vocal removal
- **Upward leveling** con `dynaudnorm` — alza le parti deboli senza schiacciare i picchi (sostituisce il compressore downward originale che tagliava i picchi)
- **Limiter** (`alimiter`) — safety net per evitare clipping
- **Stereo widening** opzionale (`-StereoWiden`) — `extrastereo=m=1.3` per compensare il collasso stereo
- **Normalizzazione** opzionale (`-Normalization peak|loudness`)

### Parametri principali

| Parametro | Tipo | Default | Descrizione |
|-----------|------|---------|-------------|
| `$InputFile` | string | obbligatorio | Traccia devocalizzata |
| `$EqLevel` | int 0-5 | obbligatorio | Intensità equalizzazione |
| `$CompLevel` | int 0-5 | obbligatorio | Intensità upward leveling |
| `$RangeSeconds` | string | — | Range manuali ("30-60,85-110") |
| `$VocalFile` | string | — | Traccia vocale per analisi auto |
| `$Sensitivity` | int 1-5 | 3 | Soglia rilevamento voce (1=solo forte, 5=anche leggera) |
| `$MinGap` | double | 1.5 | Gap minimo (sec) tra range prima di fonderli |
| `$MinDuration` | double | 0.5 | Durata minima (sec) di un range |
| `$Normalization` | string | "none" | none/peak/loudness |
| `$StereoWiden` | switch | false | Attiva extrastereo |
| `$WhatIf` | switch | false | Mostra comando senza eseguire |

### Analisi vocale — come funziona

Usa `silencedetect` di ffmpeg in una singola passata rapida. La sensitivity controlla la soglia:

- Sensitivity 1 → soglia -20dB (solo voce forte, pochi range)
- Sensitivity 3 → soglia -32.5dB (bilanciato)
- Sensitivity 5 → soglia -45dB (anche voce leggera, molti range)

L'output di silencedetect viene invertito: le zone dove la voce NON è silenziosa diventano i range dove applicare i filtri. I range vicini (gap < MinGap) vengono fusi, quelli troppo corti (< MinDuration) scartati.

### Codec supportati

mp3, flac, wav, opus, ogg, m4a, aac. Per mp3 rileva e preserva il bitrate originale.

## Problemi risolti durante lo sviluppo (lezioni apprese)

Questi sono problemi incontrati su **Windows PowerShell** (non PowerShell Core) con **locale italiana** che è importante conoscere per future modifiche:

### 1. PowerShell e stderr di ffmpeg
`$ErrorActionPreference = 'Stop'` + `2>&1` su eseguibili nativi causa crash perché PowerShell converte ogni riga stderr in un `ErrorRecord` terminante. Inoltre `2>&1 | Out-String` perde il contenuto testuale degli ErrorRecord.

**Soluzione adottata**: scrivere il comando in un file `.bat` temporaneo e eseguirlo con `cmd /c`, con redirect `2>"file.txt"` gestito da cmd.exe. Poi leggere il file con `Get-Content`.

### 2. Locale italiana e parsing decimale
ffprobe restituisce `279.97` (con punto) ma `[double]::TryParse()` in locale italiana si aspetta la virgola. `Get-AudioDuration` ora usa `CultureInfo.InvariantCulture`.

**Attenzione**: `Get-AudioBitrate` potrebbe avere lo stesso problema ma non è stato ancora corretto (il bitrate è intero quindi non è emerso).

### 3. Interpolazione stringhe PowerShell
- `"${var}s"` → PowerShell cerca `$vars`. Usare `"$($var)s"` oppure `-f` format operator.
- `"($($var))"` → le parentesi tonde attorno a `$()` confondono il parser. Usare `-f`.
- Caratteri Unicode nei commenti (es. `─`) → con encoding sbagliato diventano `â"€`. Risolto con BOM UTF-8 e/o solo ASCII.

### 4. Array di un solo elemento
Con `Set-StrictMode -Version Latest`, un array di un singolo PSCustomObject perde `.Count`. Forzare con `@(...)` e restituire con `return ,$array` (virgola unaria).

### 5. `ValidateScript` su parametri opzionali
`[ValidateScript({ if ($_) { ... } else { $true } })]` su un parametro stringa opzionale rompe il parsing di TUTTI i parametri successivi. Rimosso e sostituito con validazione manuale nel body.

### 6. `astats` con `reset=N` non stampa output ripetuto
Testato con ffmpeg 2025-01-15: `astats=reset=88200` resetta i contatori interni ma NON stampa statistiche intermedie nel log. Stampa solo il sommario finale. Per questo è stato adottato `silencedetect`.

## Idee per sviluppi futuri

### Non ancora implementate

1. **Crossfade tra segmenti** — attualmente il `concat` tra segmento processato e non processato è un taglio netto. Con molti range corti potrebbero esserci "click". Si potrebbe aggiungere `acrossfade` tra i segmenti.

2. **Loudnorm a due passate** — l'attuale normalizzazione LUFS è single-pass. Per precisione serve un primo pass di analisi e un secondo di applicazione.

3. **Intensità variabile per range** — attualmente tutti i range ricevono la stessa catena effetti. Si potrebbe modulare l'intensità in base a quanto era forte la voce in quel range (voce forte = buco più grande = compensazione più aggressiva).

4. **Batch processing** — processare più file in una cartella in una volta sola.

5. **Confronto A/B** — generare un breve campione prima/dopo per valutare rapidamente l'effetto.

## Ambiente di sviluppo

- **OS**: Windows (PowerShell 5.1, non Core)
- **Locale**: Italiana (separatore decimale: virgola)
- **ffmpeg**: 2025-01-15-git-4f3c9f2f03-full_build-www.gyan.dev
- **Tracce vocali**: estratte con UVR / Demucs / Mel-Roformer
- **Directory di lavoro tipica**: `C:\tmp\KAR\CutStartSecs\`

## Esempio di invocazione

```powershell
# Analisi automatica con sensitivity 2
.\Enhance-Devocalized.ps1 'song.mp3' 2 2 -VocalFile 'song_vocals.flac' -Sensitivity 2 -StereoWiden

# Range manuali
.\Enhance-Devocalized.ps1 'song.flac' 3 3 -RangeSeconds "30-60,85-110,150-172"

# Tutto il file con normalizzazione
.\Enhance-Devocalized.ps1 'song.wav' 2 2 -Normalization loudness

# Dry run
.\Enhance-Devocalized.ps1 'song.mp3' 2 3 -VocalFile 'vocals.wav' -Sensitivity 3 -WhatIf
```
