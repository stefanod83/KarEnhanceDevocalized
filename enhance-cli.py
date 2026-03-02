#!/usr/bin/env python3
"""
enhance-cli.py — CLI per Enhance Devocalized

Processa una traccia strumentale devocalizzata applicando compensazione
multiband STFT, senza avviare il server web.

Uso rapido:
    python enhance-cli.py mix.flac strumentale.mp3
    python enhance-cli.py --mode vocal voce.flac strumentale.mp3 --eq 5
    python enhance-cli.py --wizard
"""

import argparse
import os
import sys
import time
from pathlib import Path

# Assicura che il package backend sia importabile dalla root del progetto
sys.path.insert(0, str(Path(__file__).parent))

SUPPORTED_EXTENSIONS = {".mp3", ".flac", ".wav", ".opus", ".ogg", ".m4a", ".aac"}


def _check_file(path: str, label: str) -> str:
    """Valida che il file esiste e ha un'estensione supportata."""
    p = Path(path)
    if not p.exists():
        print(f"  ERRORE: {label} non trovato: {path}", file=sys.stderr)
        sys.exit(1)
    if p.suffix.lower() not in SUPPORTED_EXTENSIONS:
        print(
            f"  ERRORE: {label} formato non supportato '{p.suffix}'. "
            f"Supportati: {', '.join(sorted(SUPPORTED_EXTENSIONS))}",
            file=sys.stderr,
        )
        sys.exit(1)
    return str(p.resolve())


def _prompt(label: str, default: str | None = None) -> str:
    """Prompt interattivo con valore di default."""
    if default:
        raw = input(f"  {label} [{default}]: ").strip()
        return raw if raw else default
    else:
        while True:
            raw = input(f"  {label}: ").strip()
            if raw:
                return raw
            print("  (campo obbligatorio)", file=sys.stderr)


def _prompt_choice(label: str, choices: list[str], default: str) -> str:
    choices_str = "/".join(
        c.upper() if c == default else c for c in choices
    )
    while True:
        raw = input(f"  {label} [{choices_str}]: ").strip().lower()
        if not raw:
            return default
        if raw in choices:
            return raw
        print(f"  Scelte valide: {', '.join(choices)}", file=sys.stderr)


def _prompt_int(label: str, min_val: int, max_val: int, default: int) -> int:
    while True:
        raw = input(f"  {label} ({min_val}-{max_val}) [{default}]: ").strip()
        if not raw:
            return default
        try:
            val = int(raw)
            if min_val <= val <= max_val:
                return val
            print(f"  Valore fuori range ({min_val}-{max_val})", file=sys.stderr)
        except ValueError:
            print("  Inserisci un numero intero", file=sys.stderr)


def _prompt_bool(label: str, default: bool) -> bool:
    default_str = "S/n" if default else "s/N"
    raw = input(f"  {label} [{default_str}]: ").strip().lower()
    if not raw:
        return default
    return raw in ("s", "si", "sì", "y", "yes", "1", "true")


def wizard_mode() -> dict:
    """Raccolta parametri in modalità interattiva."""
    print()
    print("=" * 60)
    print("  ENHANCE DEVOCALIZED — Configurazione guidata")
    print("=" * 60)
    print()

    mode = _prompt_choice("Modalità", ["mix", "vocal"], "mix")
    print()

    if mode == "mix":
        ref_label = "Mix originale (voce + strumenti)"
    else:
        ref_label = "Traccia vocale isolata (da UVR/Demucs)"

    reference = _prompt(ref_label)
    instrumental = _prompt("Traccia strumentale (devocalizzata)")
    output = _prompt("Output (lascia vuoto per default)", default="")
    print()
    print("  Parametri di elaborazione:")

    eq = _prompt_int("  Compensazione (0=off, 10=max)", 0, 10, 7)
    bands = _prompt_int("  Bande frequenza", 6, 32, 24)

    sensitivity = 9
    if mode == "vocal":
        sensitivity = _prompt_int("  Sensibilità", 1, 10, 9)

    stereo_widen = _prompt_bool("  Stereo Widen", False)
    normalization = _prompt_choice(
        "  Normalizzazione", ["none", "peak", "loudness"], "none"
    )

    return {
        "mode": mode,
        "reference": reference,
        "instrumental": instrumental,
        "output": output or None,
        "eq": eq,
        "bands": bands,
        "sensitivity": sensitivity,
        "stereo_widen": stereo_widen,
        "normalization": normalization,
    }


def build_output_path(instrumental: str, output: str | None, mode: str) -> str:
    """Calcola il percorso di output di default."""
    if output:
        return output
    inst_path = Path(instrumental)
    return str(inst_path.parent / f"enhanced_{inst_path.name}")


def run(params: dict) -> None:
    """Esegue analisi e processing con i parametri forniti."""
    from backend.analyzer import analyze_vocal_multiband, analyze_mix_reference
    from backend.processor import process_audio
    from backend.models import BandDefinition

    mode = params["mode"]
    reference = params["reference"]
    instrumental = params["instrumental"]
    output = params["output"]
    eq_level = params["eq"]
    band_count = params["bands"]
    sensitivity = params["sensitivity"]
    stereo_widen = params["stereo_widen"]
    normalization = params["normalization"]

    print()
    print("=" * 60)
    print("  ENHANCE DEVOCALIZED")
    print("=" * 60)
    print(f"  Modalità       : {mode.upper()}")
    if mode == "mix":
        print(f"  Mix originale  : {reference}")
    else:
        print(f"  Traccia vocale : {reference}")
    print(f"  Strumentale    : {instrumental}")
    print(f"  Output         : {output}")
    print(f"  Compensazione  : {eq_level}/10  ({eq_level * 10}%)" if mode == "mix"
          else f"  EQ Level       : {eq_level}/10")
    print(f"  Bande          : {band_count}")
    if mode == "vocal":
        print(f"  Sensibilità    : {sensitivity}/10")
    print(f"  Stereo Widen   : {'sì' if stereo_widen else 'no'}")
    print(f"  Normalizzazione: {normalization}")
    print()

    # --- Analisi ---
    t0 = time.time()

    def progress(pct: int, step: str = ""):
        bar = "#" * (pct // 5) + "." * (20 - pct // 5)
        print(f"\r  [{bar}] {pct:3d}%  {step:<40}", end="", flush=True)

    print("  [1/2] Analisi multiband STFT...")
    if mode == "mix":
        intensity_matrix, frame_times, band_defs = analyze_mix_reference(
            mix_path=reference,
            instrumental_path=instrumental,
            n_bands=band_count,
            progress_callback=lambda p: progress(p, "analisi mix vs strumentale"),
        )
    else:
        intensity_matrix, frame_times, band_defs = analyze_vocal_multiband(
            vocal_path=reference,
            sensitivity=sensitivity,
            n_bands=band_count,
            progress_callback=lambda p: progress(p, "analisi vocale"),
        )
    print(f"\r  [1/2] Analisi completata in {time.time() - t0:.1f}s"
          f" — {band_count} bande × {intensity_matrix.shape[1]} frame" + " " * 20)

    # --- Processing ---
    t1 = time.time()
    print("  [2/2] Processing STFT per-banda...")

    def proc_progress(pct: int):
        progress(pct, "applicazione gain spettrale")

    process_audio(
        instrumental_path=instrumental,
        output_path=output,
        intensity_matrix=intensity_matrix,
        analysis_frame_times=frame_times,
        band_defs=band_defs,
        eq_level=eq_level,
        mode=mode,
        stereo_widen=stereo_widen,
        normalization=normalization,
        progress_callback=proc_progress,
    )
    print(f"\r  [2/2] Processing completato in {time.time() - t1:.1f}s" + " " * 40)

    total = time.time() - t0
    print()
    print(f"  Output: {output}")
    print(f"  Tempo totale: {total:.1f}s")
    print()


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="enhance-cli.py",
        description="Compensazione multiband STFT per tracce karaoke devocalizzate.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
esempi:
  # Modalità Mix (default) — usa il mix originale come riferimento
  python enhance-cli.py mix.flac strumentale.mp3

  # Con output esplicito e compensazione al 70%%
  python enhance-cli.py mix.flac strumentale.mp3 -o risultato.flac --eq 7

  # Modalità Vocale — usa la traccia vocale isolata
  python enhance-cli.py --mode vocal voce.flac strumentale.mp3 --eq 5 --sensitivity 8

  # Modalità guidata (wizard)
  python enhance-cli.py --wizard

  # Tutte le opzioni
  python enhance-cli.py mix.flac strumentale.mp3 \\
      --output enhanced.flac --eq 8 --bands 32 \\
      --stereo-widen --normalization peak
""",
    )

    parser.add_argument(
        "reference",
        nargs="?",
        metavar="REFERENCE",
        help="Mix originale (mix mode) o traccia vocale (vocal mode)",
    )
    parser.add_argument(
        "instrumental",
        nargs="?",
        metavar="INSTRUMENTAL",
        help="Traccia strumentale devocalizzata da processare",
    )
    parser.add_argument(
        "-o", "--output",
        metavar="FILE",
        help="File di output (default: enhanced_<instrumental>)",
    )
    parser.add_argument(
        "-m", "--mode",
        choices=["mix", "vocal"],
        default="mix",
        help="Modalità analisi: mix (default) o vocal",
    )
    parser.add_argument(
        "-e", "--eq",
        type=int,
        default=7,
        metavar="0-10",
        help="Livello compensazione 0-10 (default: 7). In mix mode: %% del delta (0=off, 10=100%%)",
    )
    parser.add_argument(
        "-b", "--bands",
        type=int,
        default=24,
        metavar="6-32",
        help="Numero bande frequenza logaritmiche (default: 24)",
    )
    parser.add_argument(
        "-s", "--sensitivity",
        type=int,
        default=9,
        metavar="1-10",
        help="Sensibilità rilevamento vocale 1-10 (default: 9, solo vocal mode)",
    )
    parser.add_argument(
        "--stereo-widen",
        action="store_true",
        help="Allarga l'immagine stereo nelle zone compensate",
    )
    parser.add_argument(
        "-n", "--normalization",
        choices=["none", "peak", "loudness"],
        default="none",
        help="Normalizzazione post-processing (default: none)",
    )
    parser.add_argument(
        "-w", "--wizard",
        action="store_true",
        help="Modalità guidata interattiva",
    )

    args = parser.parse_args()

    # Validazione range
    if not (0 <= args.eq <= 10):
        parser.error("--eq deve essere tra 0 e 10")
    if not (6 <= args.bands <= 32):
        parser.error("--bands deve essere tra 6 e 32")
    if not (1 <= args.sensitivity <= 10):
        parser.error("--sensitivity deve essere tra 1 e 10")

    # Wizard mode: se forzato o mancano i file obbligatori
    if args.wizard or not args.reference or not args.instrumental:
        if not args.wizard and (not args.reference or not args.instrumental):
            print("  File mancanti — avvio modalità guidata.")
        params = wizard_mode()
    else:
        params = {
            "mode": args.mode,
            "reference": args.reference,
            "instrumental": args.instrumental,
            "output": args.output,
            "eq": args.eq,
            "bands": args.bands,
            "sensitivity": args.sensitivity,
            "stereo_widen": args.stereo_widen,
            "normalization": args.normalization,
        }

    # Validazione file
    params["reference"] = _check_file(params["reference"],
        "Mix originale" if params["mode"] == "mix" else "Traccia vocale")
    params["instrumental"] = _check_file(params["instrumental"], "Strumentale")
    params["output"] = build_output_path(
        params["instrumental"], params["output"], params["mode"]
    )

    # Conferma in wizard mode
    if args.wizard or (not args.reference or not args.instrumental):
        print()
        confirm = input("  Avviare il processing? [S/n]: ").strip().lower()
        if confirm in ("n", "no"):
            print("  Annullato.")
            sys.exit(0)

    try:
        run(params)
    except KeyboardInterrupt:
        print("\n  Interrotto dall'utente.")
        sys.exit(1)
    except Exception as e:
        print(f"\n  ERRORE: {e}", file=sys.stderr)
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
