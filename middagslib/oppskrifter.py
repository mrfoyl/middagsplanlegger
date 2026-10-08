"""Oppskrifter fra Oda og egne oppskrifter, i ett felles format.

Felles format (dict):
  ref               "oda:3004" eller "egen:taco"
  navn, url
  standard_porsjoner
  minutter          int eller None
  tagger            [str]
  frysbar           bool
  ingredienser      [{ingredient_id, title, is_basic, packages_per_portion,
                      display_quantity, display_unit, product{id,name,full_name,name_extra,price,available}}]
"""
import datetime as dt
import re
import time

from . import lagring, matvarer

EGNE = "egne_oppskrifter.json"
OVERSTYR = "overstyr.json"
CACHE_DAGER = 14


def _cache_navn(oppskrift_id: int) -> str:
    return f"cache/oppskrift_{int(oppskrift_id)}.json"


def hent_oda(oda, oppskrift_id: int, bruk_cache: bool = True) -> dict:
    navn = _cache_navn(oppskrift_id)
    if bruk_cache:
        c = lagring.les(navn, None)
        if c and time.time() - c.get("_hentet", 0) < CACHE_DAGER * 86400:
            return c
    data = oda.oppskrift(oppskrift_id)
    data["_hentet"] = time.time()
    lagring.skriv(navn, data)
    return data


def fra_oda(raa: dict) -> dict:
    tagger = [t["title"] for t in raa.get("tags", [])]
    ref = f"oda:{raa['id']}"
    return {
        "ref": ref,
        "navn": raa["name"].strip(),
        "url": raa.get("url", ""),
        "standard_porsjoner": raa.get("default_portions") or 4,
        "minutter": matvarer.minutter(raa.get("duration", "")),
        "tagger": tagger,
        "hovedingrediens": next((t["title"] for t in raa.get("tags", []) if t.get("group") == "main_ingredient"), None),
        "frysbar": _frysbar(ref, raa["name"], tagger),
        "ingredienser": raa.get("ingredients", []),
    }


def _frysbar(ref, navn, tagger) -> bool:
    o = lagring.les(OVERSTYR, {}).get("frysbar", {})
    if ref in o:
        return bool(o[ref])
    return matvarer.er_frysbar(navn, tagger)


def sett_frysbar(ref: str, verdi: bool) -> None:
    o = lagring.les(OVERSTYR, {})
    o.setdefault("frysbar", {})[ref] = bool(verdi)
    lagring.skriv(OVERSTYR, o)
    if ref.startswith("egen:"):
        egne = lagring.les(EGNE, {})
        slug = ref[5:]
        if slug in egne:
            egne[slug]["frysbar"] = bool(verdi)
            lagring.skriv(EGNE, egne)


# --- egne oppskrifter ---

def slug(navn: str) -> str:
    s = matvarer.norm(navn).replace("æ", "ae").replace("ø", "o").replace("å", "a")
    return re.sub(r"[^a-z0-9]+", "-", s).strip("-") or "oppskrift"


def tolk_ingrediens(spek: str) -> dict:
    """'Kjøttdeig=12345:1' -> 1 pakke av produkt 12345 for hele oppskriften.

    'Salt' uten produkt blir en basisvare. 'Tacokrydder=:1' (tomt ID) betyr
    at varen må kjøpes, men produkt er ikke valgt ennå.
    """
    if "=" not in spek:
        return {"title": spek.strip(), "is_basic": True, "pakker": 0, "product": None}
    tittel, rest = spek.split("=", 1)
    deler = rest.split(":")
    pid = deler[0].strip()
    pakker = float(deler[1].replace(",", ".")) if len(deler) > 1 and deler[1].strip() else 1.0
    produktnavn = deler[2].strip() if len(deler) > 2 else tittel.strip()
    return {
        "title": tittel.strip(),
        "is_basic": False,
        "pakker": pakker,
        "product": {"id": int(pid), "name": produktnavn, "full_name": produktnavn, "name_extra": "", "price": 0, "available": True} if pid else None,
    }


def lag_egen(navn: str, porsjoner: int, ingredienser: list, instruksjoner: str = "", minutter: int = None, frysbar: bool = None) -> dict:
    egne = lagring.les(EGNE, {})
    s = slug(navn)
    egne[s] = {
        "navn": navn,
        "porsjoner": int(porsjoner),
        "instruksjoner": instruksjoner,
        "minutter": minutter,
        "frysbar": matvarer.er_frysbar(navn) if frysbar is None else bool(frysbar),
        "ingredienser": [tolk_ingrediens(i) for i in ingredienser],
        "oda_liste_id": egne.get(s, {}).get("oda_liste_id"),
        "endret": dt.date.today().isoformat(),
    }
    lagring.skriv(EGNE, egne)
    return egne[s] | {"slug": s}


def fra_egen(s: str, e: dict) -> dict:
    p = max(1, e["porsjoner"])
    ingr = []
    for i in e["ingredienser"]:
        ingr.append({
            "ingredient_id": None,
            "title": i["title"],
            "is_basic": i.get("is_basic", False),
            "packages_per_portion": i.get("pakker", 0) / p,
            "display_quantity": 0,
            "display_unit": "",
            "product": i.get("product"),
        })
    return {
        "ref": f"egen:{s}",
        "navn": e["navn"],
        "url": "",
        "standard_porsjoner": p,
        "minutter": e.get("minutter"),
        "tagger": ["Egen"],
        "hovedingrediens": None,
        "frysbar": bool(e.get("frysbar")),
        "ingredienser": ingr,
    }


def egne() -> dict:
    return lagring.les(EGNE, {})


def synk_egen(oda, s: str) -> dict:
    """Lagre en egen oppskrift som "dinner list" hos Oda (Oppskrifter -> Dine middager)."""
    alle = egne()
    if s not in alle:
        raise ValueError(f"Fant ingen egen oppskrift '{s}'.")
    e = alle[s]
    if e.get("oda_liste_id"):
        raise ValueError(f"'{e['navn']}' er allerede lagret hos Oda (liste {e['oda_liste_id']}).")
    beskrivelse = e.get("instruksjoner") or ""
    liste = oda.lag_middagsliste(e["navn"], beskrivelse)
    liste_id = liste["id"]
    for i in e["ingredienser"]:
        if i.get("product"):
            oda.legg_i_liste(liste_id, i["product"]["id"], max(1, round(i.get("pakker", 1) + 0.49)))
    e["oda_liste_id"] = liste_id
    lagring.skriv(EGNE, alle)
    return e


# --- oppslag ---

def hent(oda, ref: str) -> dict:
    if ref.startswith("egen:"):
        s = ref[5:]
        alle = egne()
        if s not in alle:
            raise ValueError(f"Fant ingen egen oppskrift '{s}'.")
        return fra_egen(s, alle[s])
    oid = ref[4:] if ref.startswith("oda:") else ref
    if not oid.isdigit():
        raise ValueError(f"Ugyldig oppskrift: {ref}. Bruk oda:<id> eller egen:<navn>.")
    return fra_oda(hent_oda(oda, int(oid)))


def normaliser_ref(ref: str) -> str:
    ref = ref.strip()
    if ref.isdigit():
        return f"oda:{ref}"
    if ref.startswith(("oda:", "egen:")):
        return ref
    return f"egen:{slug(ref)}"
