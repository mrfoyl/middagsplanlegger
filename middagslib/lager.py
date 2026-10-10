"""Det vi har hjemme. En enkel JSON-liste som oppdateres når Ole sier fra.

To typer oppføringer:
- vare: {"navn": "gul løk", "mengde": "1 kg" | null}
- ferdigmiddag i fryseren: {"navn": "kjøttsaus", "fryst_middag": true, "porsjoner": 4}
"""
import datetime as dt
import difflib

from . import enheter, lagring, matvarer
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


def legg_til(navn: str, mengde: str = None, fryst_middag_porsjoner: int = None, notat: str = None, sjekk: bool = False) -> dict:
    """Legg til eller oppdater en vare. Samme navn overskriver mengden."""
    varer = last()
    ny = {"navn": navn.strip(), "mengde": mengde, "lagt_til": dt.date.today().isoformat()}
    if fryst_middag_porsjoner:
        ny.update({"fryst_middag": True, "porsjoner": int(fryst_middag_porsjoner), "mengde": None})
    if notat:
        ny["notat"] = notat
    if sjekk:
        ny["sjekk"] = True
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
    # Ubekreftede (sjekk) regnes ikke med før Ole har sagt at de faktisk ligger i fryseren
    return [v for v in (varer if varer is not None else last())
            if v.get("fryst_middag") and v.get("porsjoner", 0) > 0 and not v.get("sjekk")]


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
        # Samme ord i annen rekkefølge ("rød paprika" / "paprika, rød") er samme vare
        if any(s == k or sorted(s.split()) == sorted(k.split()) for k in kandidater):
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


# --- holdbarhet og forbruk ---

def alder(vare: dict, idag: dt.date = None) -> int:
    if not vare.get("lagt_til"):
        return 0
    return ((idag or dt.date.today()) - dt.date.fromisoformat(vare["lagt_til"])).days


def holdbar(vare: dict) -> int:
    return matvarer.holdbarhet(vare["navn"])


def _kan_bli_gammel(vare: dict) -> bool:
    return not vare.get("fryst_middag") and holdbar(vare) < matvarer.TORRVARE_DAGER


def brukt(navn: str, dim: str = None, mengde: float = None, rett: str = "", uke: str = "") -> str:
    """Trekk fra det en rett brukte. Ukjent mengde -> merk varen for sjekk."""
    varer = last()
    i = _finn_indeks(varer, navn)
    if i is None:
        return None
    v = varer[i]
    har = mengde_basis(v)
    if har and dim and mengde and har[0] == dim:
        rest = har[1] - mengde
        if rest <= har[1] * 0.05:
            varer.pop(i)
            melding = f"{v['navn']}: brukt opp"
        else:
            v["mengde"] = enheter.vis(dim, rest)
            melding = f"{v['navn']}: {v['mengde']} igjen"
    else:
        v["sjekk"] = True
        v["notat"] = f"brukt i {rett} ({uke}) – sjekk om noe er igjen" if rett else "brukt – sjekk om noe er igjen"
        melding = f"{v['navn']}: brukt, sjekk om noe er igjen"
    lagre(varer)
    return melding


def rydd_utgatt(idag: dt.date = None) -> list:
    """Fjern ferskvare som har ligget dobbelt så lenge som den holder. Returnerer [(navn, dager)]."""
    varer = last()
    beholdt, fjernet = [], []
    for v in varer:
        if _kan_bli_gammel(v) and alder(v, idag) >= 2 * holdbar(v):
            fjernet.append((v["navn"], alder(v, idag)))
        else:
            beholdt.append(v)
    if fjernet:
        lagre(beholdt)
    return fjernet


def til_sjekk(idag: dt.date = None) -> list:
    """Varer Ole bør bekrefte: ferskvare forbi holdbarhet, og varer merket etter bruk."""
    ut = []
    for v in last():
        if v.get("sjekk"):
            ut.append((v["navn"], v.get("notat") or "brukt – sjekk om noe er igjen"))
        elif v.get("fryst_middag"):
            continue
        elif _kan_bli_gammel(v) and alder(v, idag) >= holdbar(v):
            ut.append((v["navn"], f"lagt inn for {alder(v, idag)} dager siden"))
    return ut


def ok(navn: str) -> bool:
    """Ole bekrefter at varen fortsatt finnes og er god: ny dato, ingen sjekk-merking."""
    varer = last()
    i = _finn_indeks(varer, navn)
    if i is None:
        return False
    v = varer[i]
    v["lagt_til"] = dt.date.today().isoformat()
    v.pop("sjekk", None)
    if (v.get("notat") or "").startswith(("brukt", "ekstra middag")):
        v.pop("notat")
    lagre(varer)
    return True


def sjekkmelding(fjernet: list, sjekk: list) -> str:
    if not fjernet and not sjekk:
        return ""
    linjer = ["*Lagersjekk*"]
    if fjernet:
        linjer.append("Fjernet fordi de har ligget lenge: " + ", ".join(f"{n} ({d} d)" for n, d in fjernet))
    if sjekk:
        linjer.append("Har dere fortsatt dette, og er det godt?")
        linjer += [f"  • {n} – {grunn}" for n, grunn in sjekk]
        linjer.append("Svar f.eks. «paprika tom, løk har vi».")
    return "\n".join(linjer)


def tekst(varer=None) -> str:
    varer = last() if varer is None else varer
    if not varer:
        return "Lageret er tomt. Legg til med: lager legg-til <vare> [--mengde \"500 g\"]"
    vanlige = sorted((v for v in varer if not v.get("fryst_middag")), key=lambda v: norm(v["navn"]))
    frys = [v for v in varer if v.get("fryst_middag") and v.get("porsjoner", 0) > 0]
    linjer = ["*Hjemme*"]
    for v in vanlige:
        linjer.append(f"  • {v['navn']}" + (f" ({v['mengde']})" if v.get("mengde") else "") + (" ❓" if v.get("sjekk") else ""))
    if frys:
        linjer.append("*Ferdigmiddager i fryseren*")
        for v in frys:
            linjer.append(f"  • {v['navn']} – {v['porsjoner']} porsjoner" + (" ❓ ikke bekreftet" if v.get("sjekk") else ""))
    return "\n".join(linjer)
