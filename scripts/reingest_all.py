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


_BACKUP_NAMES_DEFAULT = ("tickets.json", "clusters.json")
_BACKUP_NAMES_WITH_USERS = _BACKUP_NAMES_DEFAULT + ("zendesk_users.json",)


def _backup(data_dir: Path, timestamp: str, names: tuple[str, ...]) -> list[Path]:
    out: list[Path] = []
    for name in names:
        src = data_dir / name
        if not src.exists():
            continue
        dst = data_dir / f"{name}.bak-reingest-{timestamp}"
        shutil.copy2(src, dst)
        out.append(dst)
    return out


def _truncate(data_dir: Path, names: tuple[str, ...]) -> None:
    for name in names:
        default = "{}" if name == "zendesk_users.json" else "[]"
        (data_dir / name).write_text(default)


def main(argv: list[str] | None = None) -> int:
    load_dotenv()
    p = argparse.ArgumentParser()
    p.add_argument("--days", type=int, default=30)
    p.add_argument("--dry-run", action="store_true")
    p.add_argument(
        "--refresh-users",
        action="store_true",
        help="Purga data/zendesk_users.json para que la Fase 0.5 re-resuelva "
             "todos los requester_email (útil si usuarios fueron borrados o "
             "cambiaron de email en Zendesk).",
    )
    args = p.parse_args(argv)

    data_dir = Path("data")
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    names = _BACKUP_NAMES_WITH_USERS if args.refresh_users else _BACKUP_NAMES_DEFAULT

    if args.dry_run:
        print("🧪 DRY-RUN. No se escribe nada.")
        print(f"Se habría hecho backup a data/*.bak-reingest-{ts}")
        print(f"Se habría truncado: {', '.join(names)}")
        print(f"Se habría ejecutado run_pipeline(horas={args.days * 24})")
        return 0

    print(f"🛟 Backup con sufijo {ts}")
    backups = _backup(data_dir, ts, names)
    for b in backups:
        print(f"  ↳ {b}")

    print(f"🧹 Truncando: {', '.join(names)}")
    _truncate(data_dir, names)

    print(f"🚀 Ejecutando pipeline con days={args.days}")
    run_pipeline(horas=args.days * 24, dry_run=False)
    print("✅ Re-ingesta completa.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
