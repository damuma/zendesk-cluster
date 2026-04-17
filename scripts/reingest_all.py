"""Orquestador de re-ingesta completa desde Zendesk con enriquecimiento de emails.

Pasos:
1. Backup de data/{tickets,clusters}.json → data/*.bak-reingest-<timestamp>
2. Truncar tickets.json y clusters.json
3. Ejecutar pipeline (que ya integra Fase 0.5 y Fase 3.5)

Uso:
    python -m scripts.reingest_all --days 30
    python -m scripts.reingest_all --days 30 --dry-run
"""
from __future__ import annotations

import argparse
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv

from pipeline import run_pipeline


def _backup(data_dir: Path, timestamp: str) -> list[Path]:
    out: list[Path] = []
    for name in ("tickets.json", "clusters.json"):
        src = data_dir / name
        if not src.exists():
            continue
        dst = data_dir / f"{name}.bak-reingest-{timestamp}"
        shutil.copy2(src, dst)
        out.append(dst)
    return out


def _truncate(data_dir: Path) -> None:
    for name in ("tickets.json", "clusters.json"):
        (data_dir / name).write_text("[]")


def main(argv: list[str] | None = None) -> int:
    load_dotenv()
    p = argparse.ArgumentParser()
    p.add_argument("--days", type=int, default=30)
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args(argv)

    data_dir = Path("data")
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")

    if args.dry_run:
        print("🧪 DRY-RUN. No se escribe nada.")
        print(f"Se habría hecho backup a data/*.bak-reingest-{ts}")
        print("Se habría truncado tickets.json y clusters.json")
        print(f"Se habría ejecutado run_pipeline(horas={args.days * 24})")
        return 0

    print(f"🛟 Backup con sufijo {ts}")
    backups = _backup(data_dir, ts)
    for b in backups:
        print(f"  ↳ {b}")

    print("🧹 Truncando tickets.json y clusters.json")
    _truncate(data_dir)

    print(f"🚀 Ejecutando pipeline con days={args.days}")
    run_pipeline(horas=args.days * 24, dry_run=False)
    print("✅ Re-ingesta completa.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
