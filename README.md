# Enhance Devocalized

Web app per compensare i "buchi" nelle tracce karaoke devocalizzate usando analisi multiband STFT. Analizza la traccia vocale per bande di frequenza e applica compensazione EQ indipendente per ciascuna banda sulla strumentale.

## Come funziona

1. Carichi la **traccia vocale** (isolata da UVR/Demucs) e la **traccia strumentale** (devocalizzata)
2. L'app analizza la waveform vocale con STFT multiband (N bande logaritmiche da 60Hz a 16kHz)
3. Visualizzi le waveform e una heatmap delle bande di frequenza con l'intensita' dell'intervento
4. Regoli i parametri (EQ Level, Sensitivity, Bande, Stereo Widen, ecc.)
5. L'app processa la strumentale applicando gain spettrale per-banda nel dominio STFT
6. Confronti il risultato A/B e scarichi il file

## Requisiti

- Docker e Docker Compose

## Avvio rapido

```bash
docker-compose up --build
```

Apri il browser su `http://localhost:8800`

## Parametri

| Parametro | Range | Default | Descrizione |
|-----------|-------|---------|-------------|
| Sensitivity | 1-10 | 5 | Soglia rilevamento voce per banda (1=solo forte, 10=tutto) |
| Bande | 6-24 | 12 | Numero bande di frequenza logaritmiche per l'analisi |
| EQ Level | 0-10 | 5 | Intensita' compensazione per-banda (0=off, 10=max) |
| Stereo Widen | on/off | off | Allargamento stereo mid/side nelle zone vocali |
| Normalization | none/peak/loudness | none | Normalizzazione post-processing |

## Formati supportati

mp3, flac, wav, opus, ogg, m4a, aac

## Architettura

```
Browser (wavesurfer.js + canvas heatmap)  <-->  FastAPI (Python)
                                                    |
                          librosa (STFT multiband) + numpy/scipy (spectral gain)
                                                    |
                                              ffmpeg (solo conversione codec)
```

### Pipeline di processing

```
Vocale -> STFT -> magnitudine -> N bande logaritmiche -> RMS per banda -> soglia
       -> matrice intensita' 2D (N bande x T frame) -> salva .npy

Strumentale -> STFT (per canale)
            -> gain per-bin dalla matrice 2D interpolata
            -> ISTFT (ricostruzione perfetta overlap-add)
            -> stereo widen opzionale
            -> normalizzazione peak (nessun limiter)
```

- **Nessuna segmentazione**: il processing avviene nel dominio STFT su tutto il file
- **Nessun limiter**: solo clip guard lineare, nessuna distorsione tanh
- **Ricostruzione perfetta**: ISTFT con overlap-add preserva la durata originale

## Sviluppo senza Docker

```bash
# Prerequisiti: Python 3.12+, ffmpeg nel PATH
pip install -r requirements.txt
uvicorn backend.main:app --reload --port 8000
```

## Progetto originale

Lo script PowerShell originale (`Enhance-Devocalized.ps1`) e' incluso e rimane funzionante. Vedi `Enhance-Devocalized-ProjectBrief.md` per la documentazione completa dello script.
