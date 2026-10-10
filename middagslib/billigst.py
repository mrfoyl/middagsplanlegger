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
SPAR_MERPRIS = 0.15  # i sparemodus: betal opptil 15 % mer for en variant som holder to uker
FRYSEORD = ("frossen", "fryst", "frosne")

# Ord som gjør varen til noe annet enn originalen
ANNEN_VARIANT = (
    "røkt", "gravet", "gravlaks", "marinert", "krydret", "panert", "grillet", "stekt", "kokt", "ferdig",
    "frossen", "fryst", "frosne", "tørket", "hermetisk", "pålegg", "skivet", "kebab", "bolognese",
    "arrabbiata", "pesto", "chili", "hot", "sterk", "jalapeño", "hvitløk", "ost", "oster", "barnemat",
    "lettsaltet", "laktosefri", "glutenfri", "vegansk", "vegetar", "plantebasert", "snack", "dressing",
    "pulver", "mix", "smak", "saus", "lett", "light", "mager", "sukkerfri", "proteinrik",
    "sandwich", "knekkebrød", "kjeks", "chips", "syltet", "sylte", "skivede", "hakket", "hakkede",
)
# Hvis originalen nevner en av disse og kandidaten en annen fra samme gruppe, er det en annen vare
EKSKLUSIVE_GRUPPER = (
    ("storfe", "svin", "kylling", "lam", "kalkun", "hjort", "elg", "reinsdyr", "okse"),
    ("rød", "røde", "grønn", "grønne", "gul", "gule", "sort", "sorte", "hvit", "hvite", "brun", "brune"),
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


def _passer(kandidat: dict, original: dict, tittel: str, profil: dict, tillat_fryst: bool = False) -> bool:
    if not kandidat.get("availability", {}).get("is_available", True):
        return False
    tekst = f"{kandidat.get('name', '')} {kandidat.get('subtitle', '')}"
    orig_tekst = f"{original.get('full_name', '')} {original.get('name', '')} {original.get('name_extra', '')} {tittel}"
    hoved = _hovedord(tittel)
    if hoved and not _har_hovedord(tekst, hoved, orig_tekst):
        return False
    for o in ANNEN_VARIANT:
        if tillat_fryst and o in FRYSEORD:
            continue
        if matvarer.inneholder(tekst, o) and not matvarer.inneholder(orig_tekst, o):
            return False
    for gruppe in EKSKLUSIVE_GRUPPER:
        orig_har = {o for o in gruppe if matvarer.inneholder(orig_tekst, o)}
        kand_har = {o for o in gruppe if matvarer.inneholder(tekst, o)}
        if orig_har and kand_har and not (orig_har & kand_har):
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


def finn(oda, liste: dict, profil: dict, hopp_over=(), logg=lambda *_: None, spar: bool = False) -> dict:
    """{nokkel: {"produkt", "fra", "til", "spart", "antall"}} for varer der et alternativ er rimeligere.

    spar=True (sparemodus): kortholdbare varer byttes til en frossen/langholdbar
    variant når den koster høyst SPAR_MERPRIS mer enn originalen.
    """
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
        kort = spar and matvarer.holdbarhet_uapnet(x["tittel"], orig.get("full_name", "")) < matvarer.SPAR_MIN_DAGER
        sok = [_sokeord(x["tittel"])] + ([f"{_hovedord(x['tittel'])} fryst"] if kort else [])
        kandidater, sett = [], set()
        for q in sok:
            try:
                for k in _hent_sok(oda, q).get("items", [])[:MAKS_KANDIDATER]:
                    if k["id"] not in sett:
                        sett.add(k["id"])
                        kandidater.append(k)
            except OdaFeil as e:
                logg(f"Prissøk for {x['tittel']} feilet: {e}")
        beste = beste_holdbar = None
        for k in kandidater:
            if k["id"] == orig["id"] or not k.get("price"):
                continue
            st = enheter.pakkestorrelse(k.get("subtitle", ""), k.get("name", ""))
            if not st or st[0] != dim or not _passer(k, orig, x["tittel"], profil, tillat_fryst=spar):
                continue
            antall, kost = _kostnad(behov, st[1], k["price"])
            if kort and matvarer.holdbarhet_uapnet(k.get("name", ""), k.get("subtitle", "")) >= matvarer.SPAR_MIN_DAGER:
                if beste_holdbar is None or kost < beste_holdbar[2]:
                    beste_holdbar = (k, antall, kost)
            if beste is None or kost < beste[2]:
                beste = (k, antall, kost)
        holdbar_bytte = bool(beste_holdbar and beste_holdbar[2] <= orig_kost * (1 + SPAR_MERPRIS))
        if holdbar_bytte:
            beste = beste_holdbar
        if not beste:
            continue
        k, antall, kost = beste
        spart = orig_kost - kost
        if holdbar_bytte or (spart >= MIN_SPARING_KR and spart >= MIN_SPARING_ANDEL * orig_kost):
            produkt = som_produkt(k)
            bytter[x["nokkel"]] = {
                "tittel": x["tittel"],
                "produkt": produkt,
                "fra": orig.get("full_name") or orig.get("name"),
                "til": f"{produkt['name']} ({produkt['name_extra']})",
                "spart": round(spart, 2),
                "antall": antall,
                "holdbar": holdbar_bytte,
            }
    return bytter
