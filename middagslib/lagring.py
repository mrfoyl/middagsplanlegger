"""Stier og JSON-lagring for middagsplanleggeren."""
import json
import os
import tempfile
from pathlib import Path

ROT = Path(__file__).resolve().parent.parent


def datakatalog() -> Path:
    sti = Path(os.environ.get("MIDDAG_DATA", ROT / "data"))
    sti.mkdir(parents=True, exist_ok=True)
    return sti


def sti(navn: str) -> Path:
    return datakatalog() / navn


def les(navn: str, standard):
    p = sti(navn)
    if not p.exists():
        return standard
    with open(p, encoding="utf-8") as f:
        return json.load(f)


def skriv(navn: str, data) -> None:
    """Skriv atomisk, så en avbrutt kjøring aldri etterlater halv JSON."""
    p = sti(navn)
    p.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=p.parent, prefix=f".{p.name}.")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.write("\n")
    os.replace(tmp, p)
