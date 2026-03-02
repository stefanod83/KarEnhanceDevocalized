# Enhance Devocalized

Compensazione multiband STFT per tracce karaoke devocalizzate.
Analizza la differenza energetica per-banda tra il mix originale e la strumentale (o tra voce isolata e strumentale), poi applica gain spettrale indipendente per ciascuna banda di frequenza.

## Come funziona

### Modalità Mix (default, consigliata)
1. Carichi il **mix originale** (voce + strumenti) e la **strumentale devocalizzata**
2. L'app calcola per ogni banda e ogni frame il rapporto esatto `RMS_mix / RMS_strumentale`
3. Il gain adattivo per-banda compensa esattamente i livelli persi dalla devocalizzazione
4. Un **tetto adattivo per-banda** evita di amplificare residui vocali o rumore nelle zone dove la strumentale non aveva energia

### Modalità Vocale
1. Carichi la **traccia vocale isolata** (da UVR/Demucs) e la **strumentale**
2. L'app stima dove e quanto compensare dalla forma dell'inviluppo vocale per banda
3. La compensazione è proporzionale all'intensità vocale per banda

### Pipeline tecnica

```
[Mix/Vocale] ──STFT──► magnitudine ──► N bande logaritmiche
                                              │
[Strumentale] ─STFT──► magnitudine ──► RMS per-banda per-frame
                                              │
                                    gain_ratio = RMS_mix / RMS_inst
                                    adaptive_cap = f(energia_per_banda)
                                              │
                            interpolazione su frame di processing
                                              │
[Strumentale] ──STFT──► gain per-bin ──► ISTFT ──► [Output]
                        ▲
                   stereo widen + normalizzazione (opzionali)
```

- **STFT**: n_fft=2048 (~93ms), hop=512 (~23ms), 22050 Hz analisi
- **Bande**: N bande logaritmiche da 60 Hz a 16 kHz (geomspace)
- **Tetto adattivo**: per ogni banda, il boost massimo scala con l'energia della strumentale rispetto alla sua mediana in quella banda (–40 dB di range). Previene amplificazione di residui vocali nelle zone silenziose.
- **Nessun limiter**: solo clip guard tanh sui picchi oltre 0.98, nessuna distorsione

---

## Installazione

### Con Docker (consigliato)

```bash
docker-compose up --build
```

Apri il browser su `http://localhost:8800`

### Senza Docker

Requisiti: Python 3.12+, ffmpeg nel PATH

```bash
pip install -r requirements.txt
```

Per la **Web UI**:
```bash
uvicorn backend.main:app --reload --port 8000
```

Per la **CLI** non serve il server — funziona direttamente.

---

## Web UI

Apri `http://localhost:8800` (Docker) o `http://localhost:8000` (dev).

1. Seleziona la modalità (**Mix Originale** o **Traccia Vocale**)
2. Carica i file audio
3. Clicca **Analizza** — vedi la heatmap multiband
4. Regola i parametri nella sezione Processing
5. Clicca **Processa** — confronta A/B e scarica il risultato

---

## CLI

### Uso base

```bash
# Modalità Mix — usa il mix originale come riferimento (consigliata)
python enhance-cli.py mix.flac strumentale.mp3
python enhance-cli.py -b 32 -e 3 mix.flac strumentale.mp3

# Modalità Vocale — usa la traccia vocale isolata
python enhance-cli.py --mode vocal voce.flac strumentale.mp3
python enhance-cli.py --mode vocal -b 32 -s 10 -e 10 voce.flac strumentale.mp3

# Modalità guidata interattiva (wizard)
python enhance-cli.py --wizard
python enhance-cli.py -w
```

### Opzioni complete

```
uso: enhance-cli.py [OPZIONI] [REFERENCE] [INSTRUMENTAL]

argomenti posizionali:
  REFERENCE              Mix originale (mix mode) o traccia vocale (vocal mode)
  INSTRUMENTAL           Traccia strumentale devocalizzata da processare

opzioni:
  -o, --output FILE      File di output (default: enhanced_<instrumental>)
  -m, --mode {mix,vocal} Modalità analisi (default: mix)
  -e, --eq 0-10          Livello compensazione (default: 7)
                           mix mode: % del delta (0=off, 10=100%)
                           vocal mode: intensità EQ (0=off, 10=max)
  -b, --bands 6-32       Bande di frequenza logaritmiche (default: 24)
  -s, --sensitivity 1-10 Soglia rilevamento vocale, solo vocal mode (default: 9)
      --stereo-widen     Allarga l'immagine stereo nelle zone compensate
  -n, --normalization    none | peak | loudness (default: none)
  -w, --wizard           Avvia la modalità guidata interattiva
  -h, --help             Mostra questo aiuto
```

### Esempi

```bash
# Mix mode con output esplicito e compensazione al 70%
python enhance-cli.py mix.flac strumentale.mp3 -o risultato.flac --eq 7

# Mix mode massima risoluzione, con normalizzazione peak
python enhance-cli.py mix.flac strumentale.mp3 --bands 32 --normalization peak

# Vocal mode, sensibilità alta, stereo widen
python enhance-cli.py --mode vocal voce.flac strumentale.mp3 \
    --eq 5 --sensitivity 8 --stereo-widen

# Tutte le opzioni esplicite
python enhance-cli.py mix.flac strumentale.mp3 \
    --output enhanced.flac \
    --mode mix \
    --eq 8 \
    --bands 32 \
    --stereo-widen \
    --normalization loudness
```

### PowerShell (Windows)

```powershell
# Uso base
python enhance-cli.py mix.flac strumentale.mp3

# Con parametri espliciti
python enhance-cli.py mix.flac strumentale.mp3 `
    --output risultato.flac `
    --eq 8 --bands 32 `
    --normalization peak

# Wizard
python enhance-cli.py --wizard
```

> **Nota Windows**: se `python` non è nel PATH, usa `py enhance-cli.py` oppure il percorso completo dell'interprete.

---

## Parametri

| Parametro | Range | Default | Descrizione |
|-----------|-------|---------|-------------|
| `--mode` | mix / vocal | mix | **mix**: usa mix originale per gain ratio esatto. **vocal**: stima da traccia vocale isolata |
| `--eq` | 0–10 | 7 | Mix mode: % di compensazione (0=nessuna, 10=100%). Vocal mode: intensità EQ |
| `--bands` | 6–32 | 24 | Numero bande logaritmiche 60Hz–16kHz. Più bande = più risoluzione frequenziale |
| `--sensitivity` | 1–10 | 9 | Solo vocal mode. 1=solo voce forte, 10=rileva tutto |
| `--stereo-widen` | flag | off | Allargamento mid/side nelle zone compensate |
| `--normalization` | none/peak/loudness | none | **peak**: porta il picco a 0 dBFS. **loudness**: normalizza a –16 LUFS |

### Guida alla scelta del parametro `--eq` in Mix mode

| Valore | % compensazione | Quando usarlo |
|--------|-----------------|---------------|
| 0 | 0% | Nessun intervento (test) |
| 3 | 30% | Compensazione leggera, suono morbido |
| 5 | 50% | Bilanciamento tra originale e compensato |
| 7 | 70% | Default — buon risultato per la maggior parte dei brani |
| 10 | 100% | Ripristino completo dei livelli del mix originale |

### Guida alle bande

| Valore | Risoluzione | Nota |
|--------|-------------|------|
| 6–8 | Bassa | Analisi grossolana, processing veloce |
| 12–16 | Media | Buon compromesso |
| 24 | Alta (default) | Risoluzione fine, consigliata |
| 32 | Massima | Molto dettagliato, elaborazione più lenta |

---

## Formati supportati

`mp3` `flac` `wav` `opus` `ogg` `m4a` `aac`

Il formato di output segue quello del file di input (o il formato dell'`--output` specificato). La conversione codec usa ffmpeg.

---

## Architettura

```
CLI (enhance-cli.py)
    │
    ├── backend/analyzer.py   — STFT multiband, gain ratio, tetto adattivo per-banda
    ├── backend/processor.py  — Applicazione gain spettrale, ISTFT, stereo widen
    └── backend/models.py     — Modelli dati

Web UI (FastAPI + wavesurfer.js)
    │
    ├── backend/main.py       — Endpoints SSE/WebSocket
    ├── frontend/index.html   — UI
    ├── frontend/app.js       — Logica frontend
    └── frontend/style.css    — Stili
```

## Sviluppo

```bash
# Esegui i test manualmente con un file audio
python enhance-cli.py --wizard

# Avvia il server in modalità sviluppo
uvicorn backend.main:app --reload --port 8000

# Rebuild Docker
docker-compose up --build
```
