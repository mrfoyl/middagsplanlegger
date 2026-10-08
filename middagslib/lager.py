"""Det vi har hjemme. En enkel JSON-liste som oppdateres når Ole sier fra.

To typer oppføringer:
- vare: {"navn": "gul løk", "mengde": "1 kg" | null}
- ferdigmiddag i fryseren: {"navn": "kjøttsaus", "fryst_middag": true, "porsjoner": 4}
"""
import datetime as dt
import difflib

from . import enheter, lagring
from .matvarer import norm

FIL = "lager.json"

_UREGELMESSIG = {"gulrøtter": "gulrot", "gulrøttene": "gulrot", "eggene": "egg", "løkene": "løk"}


def last() -> list:
    return lagring.les(FIL, [])


def lagre(varer: list) -> None:
    lagring.skriv(FIL, varer)


def stamme(tekst: str) -> str:
    ord_ = []
    for o in norm(tekst).split():
        o = _UREGELMESSIG.get(o, o)
        for endelse in ("ene", "er", "r"):
            if len(o) > 4 and o.endswith(endelse):
                o = o[: -len(endelse)]
                break
        ord_.append(o)
    return " ".join(ord_)


def _finn_indeks(varer, navn):
    s = stamme(navn)
    for i, v in enumerate(varer):
        if stamme(v["navn"]) == s:
            return i
    return None


def legg_til(navn: str, mengde: str = None, fryst_middag_porsjoner: int = None, notat: str = None) -> dict:
    """Legg til eller oppdater en vare. Samme navn overskriver mengden."""
    varer = last()
    ny = {"navn": navn.strip(), "mengde": mengde, "lagt_til": dt.date.today().isoformat()}
    if fryst_middag_porsjoner:
        ny.update({"fryst_middag": True, "porsjoner": int(fryst_middag_porsjoner), "mengde": None})
    if notat:
        ny["notat"] = notat
    i = _finn_indeks(varer, navn)
    if i is None:
        varer.append(ny)
    else:
        varer[i] = ny
    lagre(varer)
    return ny


def fjern(navn: str) -> bool:
    varer = last()
    i = _finn_indeks(varer, navn)
    if i is None:
        return False
    varer.pop(i)
    lagre(varer)
    return True


def tom() -> int:
    n = len(last())
    lagre([])
    return n


def ferdigmiddager(varer=None) -> list:
    return [v for v in (varer if varer is not None else last()) if v.get("fryst_middag") and v.get("porsjoner", 0) > 0]


def bruk_ferdigmiddag(navn: str, porsjoner: int) -> None:
    varer = last()
    i = _finn_indeks(varer, navn)
    if i is None:
        return
    varer[i]["porsjoner"] = max(0, varer[i].get("porsjoner", 0) - porsjoner)
    if varer[i]["porsjoner"] == 0:
        varer.pop(i)
    lagre(varer)


def treff(varer: list, *navn: str):
    """Finn lagervaren som passer en ingrediens.

    Returnerer (sikkerhet, vare) der sikkerhet er "sikker" (samme vare),
    "usikker" (ligner, f.eks. "løk" mot "gul løk") eller (None, None).
    """
    kandidater = [stamme(n) for n in navn if n]
    beste = (None, None)
    for v in varer:
        if v.get("fryst_middag"):
            continue
        s = stamme(v["navn"])
        if any(s == k for k in kandidater):
            return "sikker", v
        if beste[0] is None and any(_ligner(s, k) for k in kandidater):
            beste = ("usikker", v)
    return beste


def _ligner(a: str, b: str) -> bool:
    if not a or not b:
        return False
    ta, tb = set(a.split()), set(b.split())
    if ta <= tb or tb <= ta:
        return True
    if min(len(a), len(b)) >= 4 and (a in b or b in a):
        return True
    return difflib.SequenceMatcher(None, a, b).ratio() >= 0.85


def mengde_basis(vare: dict):
    """(dimensjon, basismengde) for varens mengde, eller None om ukjent."""
    return enheter.tolk_mengde(vare.get("mengde") or "")


def tekst(varer=None) -> str:
    varer = last() if varer is None else varer
    if not varer:
        return "Lageret er tomt. Legg til med: lager legg-til <vare> [--mengde \"500 g\"]"
    vanlige = sorted((v for v in varer if not v.get("fryst_middag")), key=lambda v: norm(v["navn"]))
    frys = ferdigmiddager(varer)
    linjer = ["*Hjemme*"]
    for v in vanlige:
        linjer.append(f"  • {v['navn']}" + (f" ({v['mengde']})" if v.get("mengde") else ""))
    if frys:
        linjer.append("*Ferdigmiddager i fryseren*")
        for v in frys:
            linjer.append(f"  • {v['navn']} – {v['porsjoner']} porsjoner")
    return "\n".join(linjer)
