"""Finn rimeligste alternativ for hver vare i handlelisten.

Oda kobler hver oppskriftsingrediens til ett bestemt produkt, ofte et
merkevareprodukt. Her søker vi etter samme type vare og sammenligner hva
*ukens faktiske behov* koster: antall hele pakker × pris. Lavest kilopris er
ikke nok – en stor billig pakke kan gi dyrere handel og mer svinn.

Vi bytter bare når vi er rimelig sikre på at det er samme vare:
- hovedordet i ingrediensen (f.eks. "pastasaus") må stå i produktnavnet
- ingen ord som gjør det til en annen variant (røkt, krydret, bolognese …)
  med mindre originalen også har det
- ingen treff på familiens allergier/unngå-liste
- pakkestørrelsen må kunne leses og sammenlignes med behovet
Byttene vises i planen, og Ole kan angre med `plan original <vare>`.
"""
import math
import time

from . import enheter, lagring, matvarer
from .handleliste import TOLERANSE
from .oda import OdaFeil

CACHE_TIMER = 24
MIN_SPARING_KR = 3.0
MIN_SPARING_ANDEL = 0.05
MAKS_KANDIDATER = 24

# Ord som gjør varen til noe annet enn originalen
ANNEN_VARIANT = (
    "røkt", "gravet", "gravlaks", "marinert", "krydret", "panert", "grillet", "stekt", "kokt", "ferdig",
    "frossen", "fryst", "frosne", "tørket", "hermetisk", "pålegg", "skivet", "kebab", "bolognese",
    "arrabbiata", "pesto", "chili", "hot", "sterk", "jalapeño", "hvitløk", "ost", "oster", "barnemat",
    "lettsaltet", "laktosefri", "glutenfri", "vegansk", "vegetar", "plantebasert", "snack", "dressing",
    "pulver", "mix", "smak", "saus", "lett", "light", "mager", "sukkerfri", "proteinrik",
)


def _sokeord(tittel: str) -> str:
    return matvarer.norm(tittel)


def _hovedord(tittel: str) -> str:
    forste = tittel.split(",")[0]
    ord_ = matvarer.norm(forste).split()
    return ord_[0] if ord_ else ""


def _har_hovedord(tekst: str, hoved: str, orig_tekst: str) -> bool:
    """Hovedordet må starte et ord ("kjøttboller", "pastasaus").

    "Kyllingkjøttboller" er en annen vare enn "kjøttboller", så et forledd
    godtas bare hvis originalen har det samme sammensatte ordet.
    """
    orig_ord = set(matvarer.norm(orig_tekst).split())
    for o in matvarer.norm(tekst).split():
        if o.startswith(hoved) or (hoved in o and o in orig_ord):
            return True
    return False


def _hent_sok(oda, sok: str) -> dict:
    navn = f"cache/produkt_{matvarer.norm(sok).replace(' ', '_')}.json"
    c = lagring.les(navn, None)
    if c and time.time() - c.get("_hentet", 0) < CACHE_TIMER * 3600:
        return c
    data = oda.sok_produkt(sok)
    data["_hentet"] = time.time()
    lagring.skriv(navn, data)
    return data


def _passer(kandidat: dict, original: dict, tittel: str, profil: dict) -> bool:
    if not kandidat.get("availability", {}).get("is_available", True):
        return False
    tekst = f"{kandidat.get('name', '')} {kandidat.get('subtitle', '')}"
    orig_tekst = f"{original.get('full_name', '')} {original.get('name', '')} {original.get('name_extra', '')} {tittel}"
    hoved = _hovedord(tittel)
    if hoved and not _har_hovedord(tekst, hoved, orig_tekst):
        return False
    for o in ANNEN_VARIANT:
        if matvarer.inneholder(tekst, o) and not matvarer.inneholder(orig_tekst, o):
            return False
    if matvarer.allergen_treff(tekst, profil.get("allergier", []), profil.get("unngaa", [])):
        return False
    return True


def _kostnad(behov: float, storrelse: float, pris: float) -> tuple:
    antall = max(1, math.ceil(behov / storrelse - TOLERANSE))
    return antall, antall * pris


def som_produkt(kandidat: dict) -> dict:
    """Søketreff -> samme produktformat som oppskriftene bruker."""
    return {
        "id": kandidat["id"],
        "name": kandidat.get("name", ""),
        "full_name": kandidat.get("name", ""),
        "name_extra": kandidat.get("subtitle", ""),
        "price": kandidat.get("price", 0) or 0,
        "available": kandidat.get("availability", {}).get("is_available", True),
    }


def finn(oda, liste: dict, profil: dict, hopp_over=(), logg=lambda *_: None) -> dict:
    """{nokkel: {"produkt", "fra", "til", "spart", "antall"}} for varer der et alternativ er rimeligere."""
    bytter = {}
    for x in liste["kjop"]:
        if x["nokkel"] in hopp_over or x.get("delvis_lager") or not x.get("behov_basis"):
            continue
        dim, behov = x["behov_basis"]
        orig = x["produkt"]
        orig_st = enheter.pakkestorrelse(orig.get("name_extra", ""), orig.get("full_name", ""))
        if not orig_st or orig_st[0] != dim or not orig.get("price"):
            continue
        _, orig_kost = _kostnad(behov, orig_st[1], orig["price"])
        try:
            treff = _hent_sok(oda, _sokeord(x["tittel"]))
        except OdaFeil as e:
            logg(f"Prissøk for {x['tittel']} feilet: {e}")
            continue
        beste = None
        for k in treff.get("items", [])[:MAKS_KANDIDATER]:
            if k["id"] == orig["id"] or not k.get("price"):
                continue
            st = enheter.pakkestorrelse(k.get("subtitle", ""), k.get("name", ""))
            if not st or st[0] != dim or not _passer(k, orig, x["tittel"], profil):
                continue
            antall, kost = _kostnad(behov, st[1], k["price"])
            if beste is None or kost < beste[2]:
                beste = (k, antall, kost)
        if not beste:
            continue
        k, antall, kost = beste
        spart = orig_kost - kost
        if spart >= MIN_SPARING_KR and spart >= MIN_SPARING_ANDEL * orig_kost:
            produkt = som_produkt(k)
            bytter[x["nokkel"]] = {
                "tittel": x["tittel"],
                "produkt": produkt,
                "fra": orig.get("full_name") or orig.get("name"),
                "til": f"{produkt['name']} ({produkt['name_extra']})",
                "spart": round(spart, 2),
                "antall": antall,
            }
    return bytter
