"""Handleliste for en hel uke: slå sammen ingredienser på tvers av retter.

Kjerneideen: Oda oppgir hvor mange *pakker* av et produkt hver porsjon
trenger (0,25 pakke kremfløte per porsjon). Vi summerer brøkdelene for hele
uken per ingrediens og runder opp én gang. To retter som bruker 2 dl og 1 dl
fløte gir dermed én kartong i stedet for to, og vi ser hva som blir til overs.
"""
import datetime as dt
import math
from collections import Counter, defaultdict

from . import enheter, lager as lagermod, matvarer

TOLERANSE = 0.1     # 1,05 pakker behov -> kjøp 1 pakke, ikke 2
REST_VARSEL = 0.25  # varsle om ferskvare-rester fra en kvart pakke og opp


def nokkel(ingrediens: dict) -> str:
    if ingrediens.get("ingredient_id"):
        return f"i{ingrediens['ingredient_id']}"
    return "t:" + lagermod.stamme(ingrediens["title"])


def _behov_basis(ing: dict, faktor: float):
    """Behov i basisenhet (gram/ml/stk) for denne bruken, eller None."""
    if not ing.get("display_quantity"):
        return None
    return enheter.basis(ing["display_quantity"] * faktor, ing.get("display_unit", ""))


def _produkt_storrelse(produkt: dict):
    if not produkt:
        return None
    return enheter.pakkestorrelse(produkt.get("name_extra", ""), produkt.get("full_name", ""))


def _alltid_hjemme(tittel: str, profil: dict) -> bool:
    return any(matvarer.inneholder(tittel, o) for o in profil.get("alltid_hjemme", []))


def beregn(retter, lagervarer, profil, avklaringer=None, produktvalg=None) -> dict:
    """retter: liste av (oppskrift, porsjoner). Returnerer handleliste-dict."""
    avklaringer = avklaringer or {}
    produktvalg = produktvalg or {}

    grupper = defaultdict(list)
    for oppskrift, porsjoner in retter:
        faktor = porsjoner / max(1, oppskrift["standard_porsjoner"])
        for ing in oppskrift["ingredienser"]:
            grupper[nokkel(ing)].append({
                "ing": ing,
                "porsjoner": porsjoner,
                "faktor": faktor,
                "rett": oppskrift["navn"],
            })

    res = {"kjop": [], "fra_lager": [], "basis_hjemme": [], "usikre": [], "uten_produkt": [], "rester": [], "sum": 0.0, "svinn": 0.0}

    for k, bruk in grupper.items():
        tittel = bruk[0]["ing"]["title"]
        er_basis = all(b["ing"].get("is_basic") for b in bruk)
        retter_her = sorted({b["rett"] for b in bruk})

        produkt = produktvalg.get(k)
        if not produkt:
            teller = Counter(b["ing"]["product"]["id"] for b in bruk if b["ing"].get("product"))
            if teller:
                valgt_id = teller.most_common(1)[0][0]
                produkt = next(b["ing"]["product"] for b in bruk if b["ing"].get("product") and b["ing"]["product"]["id"] == valgt_id)

        storrelse = _produkt_storrelse(produkt)
        pakker = 0.0
        omtrentlig = False
        behov_dim, behov_mengde = None, 0.0
        for b in bruk:
            ing = b["ing"]
            behov = _behov_basis(ing, b["faktor"])
            if behov and storrelse and behov[0] == storrelse[0]:
                pakker += behov[1] / storrelse[1]
            else:
                pakker += ing.get("packages_per_portion", 0) * b["porsjoner"]
                if produkt and ing.get("product") and ing["product"]["id"] != produkt["id"]:
                    omtrentlig = True
            if behov and (behov_dim in (None, behov[0])):
                behov_dim = behov[0]
                behov_mengde += behov[1]

        fersk = matvarer.er_fersk(tittel, (produkt or {}).get("full_name", ""), basis=er_basis)
        info = {
            "nokkel": k,
            "tittel": tittel,
            "retter": retter_her,
            "basis": er_basis,
            "fersk": fersk,
            "holdbar_dager": matvarer.holdbarhet(tittel, (produkt or {}).get("full_name", ""), basis=er_basis),
            "produkt": produkt,
            "pakker_behov": round(pakker, 3),
            "behov": enheter.vis(behov_dim, behov_mengde) if behov_dim else None,
        }

        valg = avklaringer.get(k)
        if valg == "har":
            res["fra_lager"].append(info | {"merknad": "du sa vi har det"})
            continue

        treffgrad, lagervare = lagermod.treff(lagervarer, tittel, *(([produkt["name"], produkt["full_name"]]) if produkt else []))

        if treffgrad == "sikker" and valg != "kjop" and lagervare.get("lagt_til"):
            alder = (dt.date.today() - dt.date.fromisoformat(lagervare["lagt_til"])).days
            holdbar = matvarer.holdbarhet(lagervare["navn"])
            if holdbar <= matvarer.FERSK_GRENSE and alder > holdbar:
                treffgrad = "usikker"
                info["gammel_lagervare"] = f"«{lagervare['navn']}» ble lagt inn for {alder} dager siden – fortsatt bra?"

        if valg != "kjop":
            if er_basis and _alltid_hjemme(tittel, profil) and treffgrad != "sikker":
                res["basis_hjemme"].append(info)
                continue
            if treffgrad == "usikker":
                grunn = info.pop("gammel_lagervare", None) or f"lageret har «{lagervare['navn']}» – er det samme vare?"
                res["usikre"].append(info | {"grunn": grunn, "lagervare": lagervare["navn"]})
                continue
            if treffgrad is None and er_basis:
                res["usikre"].append(info | {"grunn": "basisvare – har dere nok hjemme?"})
                continue

        if treffgrad == "sikker" and valg != "kjop":
            har = lagermod.mengde_basis(lagervare)
            if har and storrelse and har[0] == storrelse[0]:
                rest_behov = pakker - har[1] / storrelse[1]
                if rest_behov <= TOLERANSE:
                    res["fra_lager"].append(info | {"merknad": f"har {lagervare['mengde']}", "lagervare": lagervare["navn"]})
                    continue
                info["delvis_lager"] = f"har {lagervare['mengde']}, kjøper resten"
                info["lagervare"] = lagervare["navn"]
                pakker = rest_behov
            elif har and behov_dim and har[0] == behov_dim:
                if har[1] >= behov_mengde * 0.9:
                    res["fra_lager"].append(info | {"merknad": f"har {lagervare['mengde']}", "lagervare": lagervare["navn"]})
                    continue
                res["usikre"].append(info | {"grunn": f"har {lagervare['mengde']}, trenger {info['behov']} – kjøpe mer?", "lagervare": lagervare["navn"]})
                continue
            else:
                merknad = f"har {lagervare['mengde']}" if lagervare.get("mengde") else "mengde ikke oppgitt – antar nok"
                res["fra_lager"].append(info | {"merknad": merknad, "lagervare": lagervare["navn"]})
                continue

        if not produkt:
            res["uten_produkt"].append(info)
            continue

        antall = max(1, math.ceil(pakker - TOLERANSE))
        rest = max(0.0, antall - pakker)
        pris = produkt.get("price", 0) or 0
        linje = info | {"antall": antall, "rest_pakker": round(rest, 2), "pris": round(antall * pris, 2), "omtrentlig": omtrentlig, "tilgjengelig": produkt.get("available", True)}
        res["kjop"].append(linje)
        res["sum"] += linje["pris"]
        if fersk and rest >= REST_VARSEL:
            verdi = rest * pris
            res["svinn"] += verdi
            res["rester"].append({"tittel": tittel, "produkt": produkt["full_name"] or produkt["name"], "rest_pakker": round(rest, 2), "verdi": round(verdi, 2), "holdbar_dager": info["holdbar_dager"]})

    res["kjop"].sort(key=lambda x: (not x["fersk"], x["tittel"].lower()))
    res["sum"] = round(res["sum"], 2)
    res["svinn"] = round(res["svinn"], 2)
    return res


def finn_nokkel(liste: dict, navn: str):
    """Finn en linje i handlelisten fra fritekst (tittel eller produktnavn)."""
    s = lagermod.stamme(navn)
    alle = [x for felt in ("usikre", "kjop", "fra_lager", "uten_produkt", "basis_hjemme") for x in liste[felt]]
    for x in alle:
        if lagermod.stamme(x["tittel"]) == s:
            return x["nokkel"], x
    for x in alle:
        navn_her = [x["tittel"]] + ([x["produkt"]["name"], x["produkt"]["full_name"]] if x.get("produkt") else [])
        if any(s and s in lagermod.stamme(n) for n in navn_her):
            return x["nokkel"], x
    return None, None
