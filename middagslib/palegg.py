"""Påleggvelger: et tilfeldig, rimelig utvalg pålegg hver uke.

Hver uke kommer de faste påleggstypene (standard gulost og brunost) pluss noen
tilfeldige typer fra resten. Typer fra forrige uke velges sist, så det
varierer. Innenfor hver type velges en tilfeldig vare blant de fire
rimeligste per kg (BILLIGSTE), og forrige ukes vare unngås når det finnes andre.

Varene finnes med vanlig produktsøk og bekreftes med Odas kategori
(Pålegg › Spekemat og salami osv.), så «salami» ikke gir salamipizza.
Svartelisten tar enkeltprodukter (produkt-ID) og ord («makrell», «leverpostei»).
Valget er tilfeldig, men fast per uke: samme uke gir samme pålegg om igjen.
"""
import random

from . import billigst, enheter, lagring, matvarer
from .oda import OdaFeil

HISTORIKK = "palegg_historikk.json"
BILLIGSTE = 4          # velg tilfeldig blant så mange av de rimeligste per kg
MAKS_PRIS = 80.0       # kr per pakke, så «rimelig per kg» ikke blir en kilosblokk
BRUNOST_ORD = ("brunost", "gudbrandsdal", "geitost", "mysost", "fløtemysost", "prim")

# Påleggstyper: søkeord hos Oda, kategorien varen må ligge i under «Pålegg»,
# og eventuelle ord som må (ord) eller ikke må (ikke_ord) stå i navnet.
TYPER = {
    "gulost": {"navn": "Gulost", "sok": ["norvegia", "gulost skiver", "jarlsberg", "gulost"], "kategori": "Ost",
               "ikke_ord": BRUNOST_ORD + ("smøreost", "revet", "tube"), "maks_pris": 130.0},
    "brunost": {"navn": "Brunost", "sok": ["brunost", "gudbrandsdalsost", "fløtemysost"], "kategori": "Ost",
                "ord": BRUNOST_ORD, "ikke_ord": ("prim",)},
    "smoreost": {"navn": "Smøreost og prim", "sok": ["smøreost", "prim"], "kategori": "Smøreost og prim"},
    "kjottpalegg": {"navn": "Kjøtt- og kyllingpålegg", "sok": ["kokt skinke", "hamburgerrygg", "servelat", "kyllingpålegg", "kalkunpålegg"],
                    "kategori": "Kjøtt- og kyllingpålegg"},
    "spekemat": {"navn": "Spekemat og salami", "sok": ["salami", "spekeskinke", "spekepølse"], "kategori": "Spekemat og salami"},
    "leverpostei": {"navn": "Leverpostei", "sok": ["leverpostei"], "kategori": "Leverpostei"},
    "salatpalegg": {"navn": "Salatpålegg", "sok": ["italiensk salat", "sennepssalat", "salatpålegg"], "kategori": "Salatpålegg"},
    "fiskepalegg": {"navn": "Fiskepålegg", "sok": ["makrell i tomat", "sardiner", "fiskepålegg", "reker i lake"],
                    "kategori": "Fiskepålegg og reker i lake"},
    "tubepalegg": {"navn": "Tubepålegg", "sok": ["kaviar", "tubepålegg", "baconost", "rekeost"], "kategori": "Tubepålegg"},
    "syltetoy": {"navn": "Syltetøy", "sok": ["syltetøy", "marmelade"], "kategori": "Syltetøy"},
    "sotpalegg": {"navn": "Søtpålegg", "sok": ["sjokoladepålegg", "nugatti", "peanøttsmør"], "kategori": "Søtpålegg"},
    "honning": {"navn": "Honning", "sok": ["honning"], "kategori": "Honning og sirup"},
}

STANDARD = {"faste": ["gulost", "brunost"], "tilfeldige": 3, "maks_pris": MAKS_PRIS,
            "svarteliste_produkter": [], "svarteliste_ord": []}


def innstillinger(p: dict) -> dict:
    """Profilens pålegginnstillinger med standardverdier. Listene er kopier."""
    inn = STANDARD | p.get("palegg", {})
    return {k: list(v) if isinstance(v, list) else v for k, v in inn.items()}


def finn_type(navn: str) -> str:
    n = matvarer.norm(navn).replace(" ", "")
    for k, t in TYPER.items():
        if n in (k, matvarer.norm(t["navn"]).replace(" ", "")):
            return k
    raise ValueError(f"Ukjent påleggstype '{navn}'. Velg blant: {', '.join(TYPER)}")


# --- historikk ---

def forrige(uke: str) -> list:
    """Pålegget fra siste uke før `uke`."""
    h = lagring.les(HISTORIKK, {})
    tidligere = sorted(u for u in h if u < uke)
    return h[tidligere[-1]] if tidligere else []


def husk(uke: str, valgt: list) -> None:
    h = lagring.les(HISTORIKK, {})
    h[uke] = [{"type": x["palegg"], "id": x["id"]} for x in valgt]
    for u in sorted(h)[:-8]:  # åtte uker holder for å variere
        del h[u]
    lagring.skriv(HISTORIKK, h)


# --- kandidater ---

def _kr_kg(k: dict):
    """Regnes ut fra pris og vekt. Odas egen kilopris er av og til feil (makrell i tomat: 22 kr/kg)."""
    st = enheter.pakkestorrelse(k.get("subtitle", ""), k.get("name", ""))
    if st and st[0] == "masse" and k.get("price"):
        return k["price"] / st[1] * 1000
    if k.get("relative_price") and k.get("relative_price_unit") == "/kg":
        return float(k["relative_price"])
    return None


def er_svartelistet(vare: dict, p: dict) -> bool:
    """For en vare i planen (id, navn)."""
    return _svartelistet({"id": vare["id"], "name": vare["navn"]}, innstillinger(p))


def _svartelistet(k: dict, inn: dict) -> bool:
    if k["id"] in inn["svarteliste_produkter"]:
        return True
    tekst = f"{k.get('name', '')} {k.get('subtitle', '')}"
    return any(matvarer.inneholder(tekst, o) for o in inn["svarteliste_ord"])


def kandidater(oda, type_: str, p: dict, logg=lambda *_: None) -> list:
    """Rimeligste varer av typen, sortert på kr/kg, med kategorien bekreftet. Høyst BILLIGSTE stk."""
    t = TYPER[type_]
    inn = innstillinger(p)
    maks = t.get("maks_pris", inn["maks_pris"])
    funnet, sett = [], set()
    for q in t["sok"]:
        try:
            treff = billigst._hent_sok(oda, q).get("items", [])
        except OdaFeil as e:
            logg(f"Påleggsøk «{q}» feilet: {e}")
            continue
        for k in treff:
            if k["id"] in sett:
                continue
            sett.add(k["id"])
            tekst = f"{k.get('name', '')} {k.get('subtitle', '')}"
            kr_kg = _kr_kg(k)
            if (not k.get("availability", {}).get("is_available", True) or not k.get("price") or k["price"] > maks
                    or kr_kg is None or _svartelistet(k, inn)
                    or any(matvarer.inneholder(tekst, o) for o in t.get("ikke_ord", ()))
                    or (t.get("ord") and not any(matvarer.inneholder(tekst, o) for o in t["ord"]))
                    or matvarer.allergen_treff(tekst, p.get("allergier", []), p.get("unngaa", []))):
                continue
            funnet.append((kr_kg, k))
    funnet.sort(key=lambda x: x[0])
    ut = []
    for kr_kg, k in funnet:
        kat = billigst._kategori(oda, k["id"], logg)
        if kat and kat[0] == "Pålegg" and t["kategori"] in kat:
            ut.append(_vare(k, type_, kr_kg))
            if len(ut) >= BILLIGSTE:
                break
    return ut


def _vare(k: dict, type_: str, kr_kg: float) -> dict:
    navn = k.get("name", "")
    if k.get("subtitle") and k["subtitle"].split(",")[-1].strip() not in navn:
        navn = f"{navn} {k['subtitle'].split(',')[-1].strip()}"
    return {"id": k["id"], "navn": navn, "antall": 1, "pris": float(k["price"]), "palegg": type_, "kr_kg": round(kr_kg)}


def _velg_vare(rng, varer: list, unngaa_ider) -> dict:
    nye = [v for v in varer if v["id"] not in unngaa_ider]
    return rng.choice(nye or varer)


# --- ukens utvalg ---

def velg(oda, p: dict, uke: str, logg=lambda *_: None) -> tuple:
    """(varer, advarsler) for uken. Varene har samme format som plan["ekstra"], pluss palegg og kr_kg."""
    inn = innstillinger(p)
    rng = random.Random(f"{uke}-palegg")
    sist = forrige(uke)
    sist_typer = {x["type"] for x in sist}
    sist_ider = {x["id"] for x in sist}
    faste = [finn_type(f) for f in inn["faste"]]
    resten = [k for k in TYPER if k not in faste]
    rng.shuffle(resten)
    resten.sort(key=lambda k: k in sist_typer)  # stabil: forrige ukes typer sist

    valgt, advarsler = [], []
    for type_ in faste:
        varer = kandidater(oda, type_, p, logg)
        if varer:
            valgt.append(_velg_vare(rng, varer, sist_ider))
        else:
            advarsler.append(f"Fant ikke noe rimelig {TYPER[type_]['navn'].lower()} denne uken.")
    antall = 0
    for type_ in resten:
        if antall >= int(inn["tilfeldige"]):
            break
        varer = kandidater(oda, type_, p, logg)
        if varer:
            valgt.append(_velg_vare(rng, varer, sist_ider))
            antall += 1
    return valgt, advarsler


def bytt(oda, p: dict, uke: str, gjeldende: dict, logg=lambda *_: None):
    """En annen vare av samme type, eller None."""
    varer = [v for v in kandidater(oda, gjeldende["palegg"], p, logg) if v["id"] != gjeldende["id"]]
    if not varer:
        return None
    return _velg_vare(random.Random(f"{uke}-palegg-{gjeldende['id']}"), varer, {x["id"] for x in forrige(uke)})


# --- svarteliste ---

def svarteliste(p: dict, verdi: str) -> dict:
    inn = innstillinger(p)
    if verdi.strip().isdigit():
        if int(verdi) not in inn["svarteliste_produkter"]:
            inn["svarteliste_produkter"].append(int(verdi))
    elif verdi.strip().lower() not in inn["svarteliste_ord"]:
        inn["svarteliste_ord"].append(verdi.strip().lower())
    p["palegg"] = inn
    return p


def fjern_fra_svarteliste(p: dict, verdi: str) -> dict:
    inn = innstillinger(p)
    if verdi.strip().isdigit():
        inn["svarteliste_produkter"] = [x for x in inn["svarteliste_produkter"] if x != int(verdi)]
    else:
        inn["svarteliste_ord"] = [x for x in inn["svarteliste_ord"] if x != verdi.strip().lower()]
    p["palegg"] = inn
    return p


def tekst(p: dict) -> str:
    inn = innstillinger(p)
    linjer = ["*Pålegg*",
              f"Faste hver uke: {', '.join(TYPER[finn_type(f)]['navn'] for f in inn['faste']) or '–'}",
              f"Tilfeldige typer i tillegg: {inn['tilfeldige']}",
              f"Maks pris per pakke: {int(inn['maks_pris'])} kr",
              f"Typer: {', '.join(TYPER)}",
              f"Svartelistede ord: {', '.join(inn['svarteliste_ord']) or '–'}",
              f"Svartelistede produkter: {', '.join(str(x) for x in inn['svarteliste_produkter']) or '–'}"]
    return "\n".join(linjer)
