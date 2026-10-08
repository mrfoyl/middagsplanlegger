"""Familieprofil: hvem som spiser, hva som må unngås, og ukerytmen."""
import math

from . import lagring

FIL = "profil.json"
UKEDAGER = ["man", "tir", "ons", "tor", "fre", "lør", "søn"]

STANDARD = {
    "medlemmer": [
        {"beskrivelse": "mann", "alder": 39},
        {"beskrivelse": "kvinne", "alder": 36},
        {"beskrivelse": "gutt", "alder": 6},
        {"beskrivelse": "jente", "alder": 3},
    ],
    "allergier": [],
    "unngaa": [],
    "liker": [],
    "middagsdager": ["man", "tir", "ons", "tor", "fre"],
    "aktivitetsdager": [],
    "kalender": {"aktiv": False, "sokeord": []},
    "maks_tid_min": 40,
    "maks_tid_aktivitetsdag_min": 20,
    "porsjoner": None,
    "barnevennlig": True,
    "skriver": "Brother-HL-L2400DW",
    "alltid_hjemme": ["salt", "havsalt", "pepper", "olje", "rapsolje", "olivenolje", "stekeolje", "sukker", "hvetemel", "vann"],
}

# Felter som er lister og kan endres med legg-til / fjern
LISTEFELT = {"allergier", "unngaa", "liker", "middagsdager", "aktivitetsdager", "alltid_hjemme"}
TALLFELT = {"maks_tid_min", "maks_tid_aktivitetsdag_min", "porsjoner"}
BOOLFELT = {"barnevennlig"}
TEKSTFELT = {"skriver"}


def last() -> dict:
    data = lagring.les(FIL, None)
    if data is None:
        data = {k: (v.copy() if isinstance(v, (list, dict)) else v) for k, v in STANDARD.items()}
        lagring.skriv(FIL, data)
    for k, v in STANDARD.items():
        data.setdefault(k, v)
    return data


def lagre(data: dict) -> None:
    lagring.skriv(FIL, data)


def porsjonsfaktor(alder: int) -> float:
    if alder >= 13:
        return 1.0
    if alder >= 9:
        return 0.85
    if alder >= 6:
        return 0.7
    if alder >= 3:
        return 0.5
    return 0.3


def porsjoner(p: dict) -> int:
    """Antall porsjoner per middag. Overstyres av feltet "porsjoner"."""
    if p.get("porsjoner"):
        return int(p["porsjoner"])
    total = sum(porsjonsfaktor(int(m.get("alder", 18))) for m in p["medlemmer"])
    return max(1, math.ceil(total))


def normaliser_dag(dag: str) -> str:
    d = dag.strip().lower()[:3]
    alias = {"lor": "lør", "son": "søn", "mon": "man", "tue": "tir", "wed": "ons", "thu": "tor", "fri": "fre", "sat": "lør", "sun": "søn"}
    d = alias.get(d, d)
    if d not in UKEDAGER:
        raise ValueError(f"Ukjent ukedag: {dag}. Bruk man/tir/ons/tor/fre/lør/søn.")
    return d


def _verdi(felt: str, verdi: str):
    if felt in TALLFELT:
        return None if verdi.lower() in ("", "auto", "ingen") else int(verdi)
    if felt in BOOLFELT:
        return verdi.lower() in ("ja", "true", "1", "på")
    return verdi


def sett(felt: str, verdi: str) -> dict:
    p = last()
    if felt in LISTEFELT:
        verdier = [v.strip() for v in verdi.split(",") if v.strip()]
        if felt in ("middagsdager", "aktivitetsdager"):
            verdier = [normaliser_dag(v) for v in verdier]
        p[felt] = verdier
    elif felt in TEKSTFELT:
        p[felt] = verdi.strip() or None
    elif felt in TALLFELT | BOOLFELT:
        p[felt] = _verdi(felt, verdi)
    elif felt == "kalender":
        p["kalender"]["aktiv"] = verdi.lower() in ("ja", "true", "1", "på")
    elif felt == "kalender_sokeord":
        p["kalender"]["sokeord"] = [v.strip() for v in verdi.split(",") if v.strip()]
    else:
        raise ValueError(f"Ukjent felt: {felt}")
    lagre(p)
    return p


def legg_til(felt: str, verdier) -> dict:
    if felt not in LISTEFELT:
        raise ValueError(f"{felt} er ikke et listefelt. Listefelt: {', '.join(sorted(LISTEFELT))}")
    p = last()
    for v in verdier:
        v = v.strip()
        if felt in ("middagsdager", "aktivitetsdager"):
            v = normaliser_dag(v)
        if v and v.lower() not in (x.lower() for x in p[felt]):
            p[felt].append(v)
    lagre(p)
    return p


def fjern(felt: str, verdier) -> dict:
    if felt not in LISTEFELT:
        raise ValueError(f"{felt} er ikke et listefelt.")
    p = last()
    ut = set()
    for v in verdier:
        v = v.strip()
        ut.add(normaliser_dag(v) if felt in ("middagsdager", "aktivitetsdager") else v.lower())
    p[felt] = [x for x in p[felt] if x.lower() not in ut]
    lagre(p)
    return p


def legg_til_medlem(beskrivelse: str, alder: int) -> dict:
    p = last()
    p["medlemmer"].append({"beskrivelse": beskrivelse, "alder": int(alder)})
    lagre(p)
    return p


def fjern_medlem(nummer: int) -> dict:
    p = last()
    if not 1 <= nummer <= len(p["medlemmer"]):
        raise ValueError(f"Ingen medlem nr. {nummer}.")
    p["medlemmer"].pop(nummer - 1)
    lagre(p)
    return p


def sett_alder(nummer: int, alder: int) -> dict:
    p = last()
    if not 1 <= nummer <= len(p["medlemmer"]):
        raise ValueError(f"Ingen medlem nr. {nummer}.")
    p["medlemmer"][nummer - 1]["alder"] = int(alder)
    lagre(p)
    return p


def tekst(p: dict) -> str:
    linjer = ["*Familieprofil*"]
    for i, m in enumerate(p["medlemmer"], 1):
        linjer.append(f"  {i}. {m['beskrivelse']}, {m['alder']} år")
    auto = "" if p.get("porsjoner") else " (beregnet)"
    linjer.append(f"Porsjoner per middag: {porsjoner(p)}{auto}")
    linjer.append(f"Allergier: {', '.join(p['allergier']) or '–'}")
    linjer.append(f"Unngå: {', '.join(p['unngaa']) or '–'}")
    linjer.append(f"Liker: {', '.join(p['liker']) or '–'}")
    linjer.append(f"Middagsdager: {', '.join(p['middagsdager'])}")
    linjer.append(f"Aktivitetsdager: {', '.join(p['aktivitetsdager']) or '–'}")
    kal = p["kalender"]
    linjer.append(f"Kalender: {'på' if kal['aktiv'] else 'av'}" + (f" (søkeord: {', '.join(kal['sokeord'])})" if kal["sokeord"] else ""))
    linjer.append(f"Maks tid: {p['maks_tid_min']} min, aktivitetsdag: {p['maks_tid_aktivitetsdag_min']} min")
    linjer.append(f"Barnevennlig (unngå sterkt): {'ja' if p['barnevennlig'] else 'nei'}")
    linjer.append(f"Skriver for oppskrifter: {p.get('skriver') or 'standardskriveren'}")
    linjer.append(f"Alltid hjemme: {', '.join(p['alltid_hjemme'])}")
    return "\n".join(linjer)
