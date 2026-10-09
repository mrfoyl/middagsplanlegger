"""Ukesplan: velg middager, fordel dem på dager, og hold styr på godkjenning.

Fordeling:
1. Aktivitetsdager får først ferdigmiddager fra fryseren.
2. Resten av aktivitetsdagene kobles til en vanlig dag tidligere i uken der vi
   lager dobbel porsjon av en frysbar rett. Ligger den bare 1–2 dager før,
   holder kjøleskapet; ellers fryses den ned.
3. Aktivitetsdager uten en tidligere vanlig dag får en rask rett.
4. Vanlige dager sorteres slik at retter med kortest holdbare råvarer
   (fisk, kjøttdeig, urter) kommer først etter levering.
5. Hvis handlelisten (pluss det som allerede ligger i Oda-kurven) ikke når
   familiens minstebeløp, legges en eller flere ekstra frysbare middager til
   i fryseren – se sikre_minstebelop().

Utvalget er grådig: hver ny rett velges ut fra hva den gjør med *hele* ukens
handleliste – ekstra kostnad, ferskvare som blir liggende, og variasjon.
"""
import datetime as dt
import hashlib
import json
import math

from . import billigst, enheter, handleliste, lagring, matvarer, oppskrifter, profil as profilmod
from . import lager as lagermod
from .oda import Oda, OdaFeil

PLAN = "plan.json"
HISTORIKK = "historikk.json"
SOKECACHE_TIMER = 72

STANDARDSOK = ["kylling", "kjøttdeig", "laks", "torsk", "pasta", "suppe", "gryte", "vegetar", "taco", "fisk", "ovnsbakt", "kjøttboller", "wok", "pølser", "lasagne", "svin"]

VEKT_KOSTNAD = 0.3
STRAFF_SAMME_HOVEDINGREDIENS = 40
STRAFF_SAMME_RETTSTYPE = 60
STRAFF_NYLIG_BRUKT = 100
STRAFF_STERK = 30
BONUS_LIKER = 25
STRAFF_PER_SPORSMAL = 3


# --- uker og datoer ---

def neste_uke(idag: dt.date = None) -> str:
    idag = idag or dt.date.today()
    mandag = idag + dt.timedelta(days=7 - idag.weekday())
    aar, uke, _ = mandag.isocalendar()
    return f"{aar}-W{uke:02d}"


def datoer(uke: str) -> dict:
    aar, nr = uke.split("-W")
    mandag = dt.date.fromisocalendar(int(aar), int(nr), 1)
    return {d: (mandag + dt.timedelta(days=i)).isoformat() for i, d in enumerate(profilmod.UKEDAGER)}


def _indeks(dag: str) -> int:
    return profilmod.UKEDAGER.index(dag)


# --- kandidater ---

def _sok(oda, sok: str) -> dict:
    navn = f"cache/sok_{oppskrifter.slug(sok)}.json"
    c = lagring.les(navn, None)
    if c and dt.datetime.now().timestamp() - c.get("_hentet", 0) < SOKECACHE_TIMER * 3600:
        return c
    data = oda.sok_oppskrift(sok)
    if not data.get("items"):
        data = oda.sok_oppskrift(sok, filtre=())
    data["_hentet"] = dt.datetime.now().timestamp()
    lagring.skriv(navn, data)
    return data


def egnet(r: dict, p: dict, maks_tid: int = None):
    """(True, "") eller (False, grunn)."""
    tekster = [r["navn"]] + [i["title"] for i in r["ingredienser"]] + [
        (i.get("product") or {}).get("full_name", "") for i in r["ingredienser"]
    ]
    treff = matvarer.allergen_treff(" | ".join(tekster), p.get("allergier", []), p.get("unngaa", []))
    if treff:
        return False, f"inneholder {', '.join(sorted(set(treff)))}"
    if maks_tid and r.get("minutter") and r["minutter"] > maks_tid:
        return False, f"tar {r['minutter']} min"
    utsolgt = [i for i in r["ingredienser"] if not i.get("is_basic") and i.get("product") and not i["product"].get("available", True)]
    if len(utsolgt) >= 2:
        return False, "flere varer utsolgt"
    return True, ""


def kandidater(oda, p: dict, uke: str, maks: int = 30, logg=lambda *_: None) -> list:
    # Roter søkene per uke så forslagene varierer
    nr = int(uke.split("-W")[1])
    start = nr % len(STANDARDSOK)
    sok = p.get("liker", []) + [s for s in STANDARDSOK[start:] + STANDARDSOK[:start] if s not in p.get("liker", [])]

    ider = []
    per_sok = max(2, math.ceil(maks / max(1, len(sok))) + 1)
    for s in sok:
        if len(ider) >= maks:
            break
        try:
            treff = _sok(oda, s)
        except OdaFeil as e:
            logg(f"Søk '{s}' feilet: {e}")
            continue
        n = 0
        for item in treff.get("items", []):
            if item["id"] not in ider and n < per_sok:
                ider.append(item["id"])
                n += 1

    ut, avvist = [], []
    for oid in ider[:maks]:
        try:
            r = oppskrifter.fra_oda(oppskrifter.hent_oda(oda, oid))
        except OdaFeil as e:
            logg(f"Oppskrift {oid} feilet: {e}")
            continue
        ok, grunn = egnet(r, p)
        (ut if ok else avvist).append(r if ok else (r["navn"], grunn))
    for s, e in oppskrifter.egne().items():
        r = oppskrifter.fra_egen(s, e)
        ok, grunn = egnet(r, p)
        (ut if ok else avvist).append(r if ok else (r["navn"], grunn))
    if avvist:
        logg("Avvist: " + "; ".join(f"{n} ({g})" for n, g in avvist[:12]))
    return ut


# --- utvalg ---

def _jitter(uke: str, ref: str) -> float:
    return int(hashlib.sha1(f"{uke}{ref}".encode()).hexdigest()[:6], 16) / 0xFFFFFF


def _poeng(kand, valgte, porsjoner, p, lagervarer, historikk, uke) -> float:
    liste = handleliste.beregn(valgte + [(kand, porsjoner)], lagervarer, p)
    s = liste["svinn"] + VEKT_KOSTNAD * liste["sum"] + STRAFF_PER_SPORSMAL * len(liste["usikre"])
    hoved = kand.get("hovedingrediens")
    if hoved and any(v.get("hovedingrediens") == hoved for v, _ in valgte):
        s += STRAFF_SAMME_HOVEDINGREDIENS
    typer = matvarer.rettstyper(kand["navn"])
    if typer and any(typer & matvarer.rettstyper(v["navn"]) for v, _ in valgte):
        s += STRAFF_SAMME_RETTSTYPE
    if kand["ref"] in historikk:
        s += STRAFF_NYLIG_BRUKT
    if p.get("barnevennlig") and matvarer.er_sterk(kand["navn"] + " " + " ".join(i["title"] for i in kand["ingredienser"])):
        s += STRAFF_STERK
    tekst = kand["navn"] + " " + " ".join(kand.get("tagger", []))
    if any(matvarer.inneholder(tekst, l) for l in p.get("liker", [])):
        s -= BONUS_LIKER
    return s + 20 * _jitter(uke, kand["ref"])


def velg(kandidater_, antall, porsjoner, p, lagervarer, valgte, historikk, uke, krav=lambda r: True) -> list:
    """Velg `antall` retter grådig. `valgte` er [(oppskrift, porsjoner)] som allerede er med."""
    valgte = list(valgte)
    nye = []
    brukt = {v["ref"] for v, _ in valgte}
    for _ in range(antall):
        aktuelle = [k for k in kandidater_ if k["ref"] not in brukt and krav(k)]
        if not aktuelle:
            break
        beste = min(aktuelle, key=lambda k: _poeng(k, valgte, porsjoner, p, lagervarer, historikk, uke))
        valgte.append((beste, porsjoner))
        nye.append(beste)
        brukt.add(beste["ref"])
    return nye


def _korteste_holdbarhet(r: dict) -> int:
    dager = [matvarer.holdbarhet(i["title"], (i.get("product") or {}).get("full_name", "")) for i in r["ingredienser"] if not i.get("is_basic")]
    return min(dager) if dager else 365


# --- historikk ---

def nylig_brukt(uke: str, antall_uker: int = 3) -> set:
    h = lagring.les(HISTORIKK, {})
    aar, nr = uke.split("-W")
    mandag = dt.date.fromisocalendar(int(aar), int(nr), 1)
    ut = set()
    for u, refs in h.items():
        try:
            a, n = u.split("-W")
            m = dt.date.fromisocalendar(int(a), int(n), 1)
        except ValueError:
            continue
        if 0 < (mandag - m).days <= antall_uker * 7:
            ut.update(refs)
    return ut


def _lagre_historikk(plan: dict) -> None:
    h = lagring.les(HISTORIKK, {})
    h[plan["uke"]] = sorted({d["ref"] for d in plan["dager"] if d.get("ref") and d["type"] in ("lag", "lag_dobbel")}
                            | {x["ref"] for x in plan.get("ekstra_middager", [])})
    lagring.skriv(HISTORIKK, h)


# --- lag plan ---

def _dag(dag, dato, aktivitet):
    return {"dag": dag, "dato": dato, "aktivitet": aktivitet, "type": "mangler", "ref": None, "navn": None, "porsjoner": 0}


def _sett_rett(d: dict, r: dict, porsjoner: int, type_: str = "lag") -> None:
    d.update({"type": type_, "ref": r["ref"], "navn": r["navn"], "porsjoner": porsjoner, "minutter": r.get("minutter"), "frysbar": r.get("frysbar", False), "url": r.get("url", "")})


def lag(oda, p: dict, uke: str, ekstra_aktivitet=(), kalenderdager=(), onsket=(), maks_kandidater=30, logg=lambda *_: None) -> dict:
    dato = datoer(uke)
    porsjoner = profilmod.porsjoner(p)
    middagsdager = [d for d in profilmod.UKEDAGER if d in p["middagsdager"]]
    aktiv = set(p.get("aktivitetsdager", [])) | set(ekstra_aktivitet) | set(kalenderdager)
    dager = {d: _dag(d, dato[d], d in aktiv) for d in middagsdager}
    advarsler = []

    # 1. ferdigmiddager fra fryseren på aktivitetsdager
    frys = [dict(v) for v in lagermod.ferdigmiddager()]
    for d in middagsdager:
        if not dager[d]["aktivitet"]:
            continue
        for v in frys:
            if v["porsjoner"] >= math.ceil(porsjoner * 0.75):
                dager[d].update({"type": "ferdigmiddag", "navn": v["navn"], "porsjoner": min(v["porsjoner"], porsjoner)})
                v["porsjoner"] -= dager[d]["porsjoner"]
                break

    # 2. koble resterende aktivitetsdager til en tidligere vanlig dag
    vanlige = [d for d in middagsdager if not dager[d]["aktivitet"]]
    par = {}  # kokedag -> restdag
    raske = []
    for d in middagsdager:
        if not dager[d]["aktivitet"] or dager[d]["type"] == "ferdigmiddag":
            continue
        tidligere = [v for v in vanlige if _indeks(v) < _indeks(d) and v not in par]
        if tidligere:
            par[tidligere[-1]] = d
        else:
            raske.append(d)
    # Aktivitetsdager uten en tidligere vanlig dag (f.eks. mandag) får en rask rett
    # denne uken, og vi lager dobbel porsjon senere i uken som fryses til neste uke.
    for d in raske:
        senere = [v for v in vanlige if v not in par]
        if senere:
            par[senere[-1]] = f"neste:{d}"

    # 3. velg retter
    lagervarer = lagermod.last()
    historikk = nylig_brukt(uke)
    onskede = []
    for ref in onsket:
        try:
            onskede.append(oppskrifter.hent(oda, oppskrifter.normaliser_ref(ref)))
        except (OdaFeil, ValueError) as e:
            advarsler.append(f"Kunne ikke hente {ref}: {e}")

    pool = None

    def hent_pool():
        nonlocal pool
        if pool is None:
            pool = kandidater(oda, p, uke, maks_kandidater, logg)
        return pool

    maks_tid = p.get("maks_tid_min")
    innen_tid = lambda r: not maks_tid or not r.get("minutter") or r["minutter"] <= maks_tid
    valgte = []  # [(oppskrift, porsjoner)] for handlelisteberegning

    # Frysbare retter til dobbel porsjon
    frys_retter = [r for r in onskede if r["frysbar"]][: len(par)]
    if len(frys_retter) < len(par):
        frys_retter += velg(hent_pool(), len(par) - len(frys_retter), porsjoner * 2, p, lagervarer,
                            [(r, porsjoner * 2) for r in frys_retter], historikk, uke,
                            krav=lambda r: r["frysbar"] and r not in onskede and innen_tid(r))
    valgte += [(r, porsjoner * 2) for r in frys_retter]
    kokedager = sorted(par, key=_indeks)
    for kokedag in kokedager[len(frys_retter):]:
        restdag = par.pop(kokedag)
        if restdag.startswith("neste:"):
            advarsler.append(f"Fant ingen frysbar rett å lage dobbelt av til fryseren for neste {restdag[6:]}.")
        else:
            advarsler.append(f"Fant ingen frysbar rett til dobbel porsjon {kokedag} → {restdag}; {restdag} får en rask rett i stedet.")
            raske.append(restdag)
    kokedager = sorted(par, key=_indeks)

    # Raske retter til aktivitetsdager uten rest
    grense = p["maks_tid_aktivitetsdag_min"]
    er_rask = lambda r: bool(r.get("minutter")) and r["minutter"] <= grense
    raske_retter = [r for r in onskede if r not in frys_retter and er_rask(r)][: len(raske)]
    if len(raske_retter) < len(raske):
        raske_retter += velg(hent_pool(), len(raske) - len(raske_retter), porsjoner, p, lagervarer,
                             valgte + [(r, porsjoner) for r in raske_retter], historikk, uke, krav=er_rask)
    valgte += [(r, porsjoner) for r in raske_retter]

    # Vanlige dager
    antall_vanlige = len([d for d in vanlige if d not in par])
    vanlige_retter = [r for r in onskede if r not in frys_retter and r not in raske_retter]
    if len(vanlige_retter) > antall_vanlige:
        advarsler.append(f"For mange ønskede retter; brukte de {antall_vanlige} første.")
        vanlige_retter = vanlige_retter[:antall_vanlige]
    if len(vanlige_retter) < antall_vanlige:
        vanlige_retter += velg(hent_pool(), antall_vanlige - len(vanlige_retter), porsjoner, p, lagervarer,
                               valgte + [(r, porsjoner) for r in vanlige_retter], historikk, uke, krav=innen_tid)

    # 4. fordel på dager
    for kokedag, r in zip(kokedager, sorted(frys_retter, key=_korteste_holdbarhet)):
        restdag = par[kokedag]
        _sett_rett(dager[kokedag], r, porsjoner, "lag_dobbel")
        if restdag.startswith("neste:"):
            dager[kokedag]["frys_til"] = restdag[6:]
            continue
        avstand = _indeks(restdag) - _indeks(kokedag)
        dager[kokedag]["rest_til"] = restdag
        dager[restdag].update({"type": "rest", "ref": r["ref"], "navn": r["navn"], "porsjoner": porsjoner, "fra_dag": kokedag,
                               "lagring": "kjøleskap" if avstand <= 2 else "fryser"})
    ledige = [d for d in vanlige if d not in par]
    for d, r in zip(ledige, sorted(vanlige_retter, key=_korteste_holdbarhet)):
        _sett_rett(dager[d], r, porsjoner)
    for d, r in zip(sorted(raske, key=_indeks), raske_retter):
        _sett_rett(dager[d], r, porsjoner)
    for d in middagsdager:
        if dager[d]["type"] == "mangler":
            advarsler.append(f"Fant ingen passende rett til {d}. Sett en med: plan bytt {d} <oppskrift>")

    plan = {
        "uke": uke,
        "opprettet": dt.datetime.now().isoformat(timespec="minutes"),
        "status": "utkast",
        "porsjoner": porsjoner,
        "dager": [dager[d] for d in middagsdager],
        "avklaringer": {},
        "produktvalg": {},
        "godkjent_hash": None,
        "advarsler": advarsler,
        "kurvlogg": [],
        "bytter": {},
        "ekstra": faste_varer(p, lagermod.last()),
        "ekstra_middager": [],
    }
    lagre(plan)
    optimaliser_priser(oda, plan, p, logg)
    # Prisoptimalisering kan presse summen under minstebeløpet igjen (billigere varer på de
    # nye rettene), så vi veksler mellom de to til ingen flere ekstra middager trengs.
    for _ in range(5):
        if not sikre_minstebelop(oda, plan, p, maks_kandidater, logg):
            break
        optimaliser_priser(oda, plan, p, logg)
    return plan


# --- rimeligste alternativ ---

def optimaliser_priser(oda, plan: dict, p: dict, logg=lambda *_: None) -> list:
    """Bytt til rimeligste likeverdige produkt for varer vi ikke allerede har valgt produkt for."""
    liste = handleliste_for(oda, plan, p)
    hopp_over = set(plan.get("produktvalg", {})) | set(plan.get("behold_original", []))
    nye = billigst.finn(oda, liste, p, hopp_over, logg)
    if not nye:
        return []
    for nokkel, b in nye.items():
        plan["produktvalg"][nokkel] = b["produkt"]
        plan.setdefault("bytter", {})[nokkel] = {k: b[k] for k in ("tittel", "fra", "til", "spart")}
    _endret(plan)
    lagre(plan)
    return list(nye.values())


def faste_varer(p: dict, lagervarer: list) -> list:
    """Profilens faste ukevarer. Finnes noe som ligner i lageret, spør vi før de kjøpes."""
    ut = []
    for x in p.get("faste_varer", []):
        e = dict(x)
        grad, v = lagermod.treff(lagervarer, x["navn"])
        if grad:
            mengde = f" ({v['mengde']})" if v.get("mengde") else ""
            e["sjekk"] = f"lageret har «{v['navn']}»{mengde} – trenger dere mer?"
        ut.append(e)
    return ut


def _velg_lagedag(plan: dict) -> str:
    """Dag å lage en ekstra fryse-middag: den raskeste vanlige middagen i uken."""
    vanlige = [d for d in plan["dager"] if d["type"] == "lag" and not d.get("aktivitet")]
    if not vanlige:
        vanlige = [d for d in plan["dager"] if d["type"] in ("lag", "lag_dobbel")]
    if not vanlige:
        return plan["dager"][-1]["dag"]
    return min(vanlige, key=lambda d: (d.get("minutter") or 99, -_indeks(d["dag"])))["dag"]


def sikre_minstebelop(oda, plan: dict, p: dict, maks_kandidater: int = 30, logg=lambda *_: None) -> list:
    """Legg til ekstra frysbare middager hvis handlelisten (+ det som allerede ligger i kurven)
    havner under Oda sitt minstebeløp, så vi slipper tillegget for mindre bestillinger."""
    grense = p.get("min_bestilling_kr") or 1300
    egne_ider = {x["produkt"]["id"] for x in handleliste_for(oda, plan, p)["kjop"]} | {x["id"] for x in plan.get("ekstra", [])}
    try:
        oda.sikre_innlogget()
        i_kurv = Oda.belop_etter_rabatt_uten(oda.kurv(), egne_ider)
    except OdaFeil as e:
        logg(f"Fant ikke kurven for minstebeløp-sjekk: {e}")
        i_kurv = 0

    # Faste ukevarer (bleier, brød, melk osv.) legges også i kurven og teller mot
    # minstebeløpet, selv om de ikke er med i handleliste-summen for middagsrettene.
    ekstra_sum = sum(x.get("antall", 1) * x.get("pris", 0) for x in plan.get("ekstra", []))

    historikk = nylig_brukt(plan["uke"])
    lagervarer = lagermod.last()
    maks_tid = p.get("maks_tid_min")
    innen_tid = lambda r: not maks_tid or not r.get("minutter") or r["minutter"] <= maks_tid
    pool = None
    lagt_til = []
    for _ in range(10):
        liste = handleliste_for(oda, plan, p)
        if i_kurv + liste["sum"] + ekstra_sum >= grense:
            break
        if pool is None:
            pool = kandidater(oda, p, plan["uke"], maks_kandidater, logg)
        nye = velg(pool, 1, plan["porsjoner"], p, lagervarer, retter(oda, plan), historikk, plan["uke"],
                   krav=lambda r: r.get("frysbar") and innen_tid(r))
        if not nye:
            break
        r = nye[0]
        plan.setdefault("ekstra_middager", []).append({
            "ref": r["ref"], "navn": r["navn"], "porsjoner": plan["porsjoner"],
            "minutter": r.get("minutter"), "url": r.get("url", ""), "frysbar": True,
            "lagedag": _velg_lagedag(plan),
        })
        lagt_til.append(r["navn"])

    if lagt_til:
        plan["advarsler"].append(
            f"La til {len(lagt_til)} ekstra middag(er) i fryseren for å nå minstebeløpet "
            f"({', '.join(lagt_til)}): kurven var under {int(grense)} kr."
        )
        _endret(plan)
        lagre(plan)
    return lagt_til


def original(oda, plan: dict, p: dict, vare: str) -> str:
    """Angre et prisbytte: bruk produktet oppskriften selv peker på."""
    for nokkel, b in plan.get("bytter", {}).items():
        if lagermod.stamme(vare) in lagermod.stamme(b["tittel"]) or lagermod.stamme(b["tittel"]) in lagermod.stamme(vare):
            plan["produktvalg"].pop(nokkel, None)
            plan["bytter"].pop(nokkel)
            plan.setdefault("behold_original", []).append(nokkel)
            _endret(plan)
            lagre(plan)
            return b["fra"]
    raise ValueError(f"Fant ikke noe prisbytte for '{vare}'.")


# --- lagring og status ---

def last() -> dict:
    plan = lagring.les(PLAN, None)
    if not plan:
        raise ValueError("Ingen plan ennå. Lag en med: plan lag")
    return plan


def lagre(plan: dict) -> None:
    lagring.skriv(PLAN, plan)


def _hash(plan: dict) -> str:
    innhold = {k: plan[k] for k in ("uke", "dager", "avklaringer", "produktvalg")} | {"ekstra": plan.get("ekstra", []), "ekstra_middager": plan.get("ekstra_middager", [])}
    return hashlib.sha256(json.dumps(innhold, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:16]


def _endret(plan: dict) -> None:
    """Enhver endring etter godkjenning krever ny godkjenning."""
    if plan["status"] == "godkjent":
        plan["status"] = "utkast"
    plan["godkjent_hash"] = None


def retter(oda, plan: dict) -> list:
    ut = []
    for d in plan["dager"]:
        if d["type"] in ("lag", "lag_dobbel") and d.get("ref"):
            r = oppskrifter.hent(oda, d["ref"])
            ut.append((r, d["porsjoner"] * (2 if d["type"] == "lag_dobbel" else 1)))
    for x in plan.get("ekstra_middager", []):
        ut.append((oppskrifter.hent(oda, x["ref"]), x["porsjoner"]))
    return ut


def handleliste_for(oda, plan: dict, p: dict) -> dict:
    return handleliste.beregn(retter(oda, plan), lagermod.last(), p, plan.get("avklaringer"), plan.get("produktvalg"))


def godkjenn(oda, plan: dict, p: dict) -> dict:
    liste = handleliste_for(oda, plan, p)
    if liste["usikre"]:
        navn = ", ".join(u["tittel"] for u in liste["usikre"])
        raise ValueError(f"Avklar først: {navn}. Bruk: plan avklar <vare> har|kjop")
    faste_sporsmal = [x["navn"] for x in plan.get("ekstra", []) if x.get("sjekk")]
    if faste_sporsmal:
        raise ValueError(f"Avklar først de faste varene: {', '.join(faste_sporsmal)}. Bruk: plan avklar <vare> har|kjop")
    mangler = [d["dag"] for d in plan["dager"] if d["type"] == "mangler"]
    if mangler:
        raise ValueError(f"Dager uten middag: {', '.join(mangler)}. Bruk plan bytt <dag> <oppskrift> eller plan fri <dag>.")
    if plan["status"] in ("i_kurv", "delvis_i_kurv"):
        raise ValueError("Planen er allerede lagt i kurven.")
    plan["status"] = "godkjent"
    plan["godkjent_hash"] = _hash(plan)
    lagre(plan)
    return liste


def er_godkjent(plan: dict) -> bool:
    return plan["status"] == "godkjent" and plan.get("godkjent_hash") == _hash(plan)


# --- redigering ---

def _finn_dag(plan, dag):
    dag = profilmod.normaliser_dag(dag)
    for d in plan["dager"]:
        if d["dag"] == dag:
            return d
    raise ValueError(f"{dag} er ikke en middagsdag i planen.")


def _lost_par(plan, d):
    """Løs opp en dobbel/rest-kobling når en av dagene endres."""
    if d["type"] == "lag_dobbel" and d.get("rest_til"):
        rest = _finn_dag(plan, d["rest_til"])
        if rest["type"] == "rest":
            rest.update({"type": "mangler", "ref": None, "navn": None, "fra_dag": None, "lagring": None})
    if d["type"] == "rest" and d.get("fra_dag"):
        koke = _finn_dag(plan, d["fra_dag"])
        if koke["type"] == "lag_dobbel":
            koke["type"] = "lag"
            koke.pop("rest_til", None)
    for k in ("rest_til", "fra_dag", "lagring", "frys_til"):
        d.pop(k, None)


def bytt(oda, plan: dict, dag: str, ref: str) -> list:
    d = _finn_dag(plan, dag)
    r = oppskrifter.hent(oda, oppskrifter.normaliser_ref(ref))
    meldinger = []
    if d["type"] == "lag_dobbel" and not d.get("rest_til"):
        _sett_rett(d, r, plan["porsjoner"], "lag_dobbel")
        if not r["frysbar"]:
            meldinger.append(f"OBS: {r['navn']} er ikke merket frysbar, men ekstraporsjonen skal fryses.")
    elif d["type"] == "lag_dobbel":
        _sett_rett(d, r, plan["porsjoner"], "lag_dobbel")
        rest = _finn_dag(plan, d["rest_til"])
        rest.update({"ref": r["ref"], "navn": r["navn"]})
        if not r["frysbar"] and rest.get("lagring") == "fryser":
            meldinger.append(f"OBS: {r['navn']} er ikke merket frysbar, men resten skal fryses til {rest['dag']}.")
    else:
        if d["type"] == "rest":
            _lost_par(plan, d)
        _sett_rett(d, r, plan["porsjoner"])
    _endret(plan)
    lagre(plan)
    meldinger += [f"Rimeligere: {b['tittel']} → {b['til']} (sparer {b['spart']:.0f} kr)" for b in optimaliser_priser(oda, plan, profilmod.last())]
    return meldinger


def dobbel(plan: dict, kokedag: str, restdag: str) -> None:
    k, r = _finn_dag(plan, kokedag), _finn_dag(plan, restdag)
    if _indeks(k["dag"]) >= _indeks(r["dag"]):
        raise ValueError("Dagen du lager dobbel porsjon må komme før restedagen.")
    if k["type"] not in ("lag", "lag_dobbel") or not k.get("ref"):
        raise ValueError(f"{k['dag']} har ingen rett å lage dobbel av.")
    _lost_par(plan, r)
    if k["type"] == "lag_dobbel" and k.get("rest_til") and k["rest_til"] != r["dag"]:
        _lost_par(plan, k)
    avstand = _indeks(r["dag"]) - _indeks(k["dag"])
    k.update({"type": "lag_dobbel", "rest_til": r["dag"]})
    r.update({"type": "rest", "ref": k["ref"], "navn": k["navn"], "porsjoner": plan["porsjoner"], "fra_dag": k["dag"],
              "lagring": "kjøleskap" if avstand <= 2 else "fryser"})
    _endret(plan)
    lagre(plan)


def fri(plan: dict, dag: str) -> None:
    d = _finn_dag(plan, dag)
    _lost_par(plan, d)
    d.update({"type": "fri", "ref": None, "navn": None, "porsjoner": 0})
    _endret(plan)
    lagre(plan)


def ferdigmiddag(plan: dict, dag: str, navn: str) -> None:
    d = _finn_dag(plan, dag)
    _lost_par(plan, d)
    d.update({"type": "ferdigmiddag", "ref": None, "navn": navn, "porsjoner": plan["porsjoner"]})
    _endret(plan)
    lagre(plan)


def avklar(oda, plan: dict, p: dict, vare: str, svar: str) -> dict:
    svar = {"ja": "har", "har": "har", "nei": "kjop", "kjøp": "kjop", "kjop": "kjop"}.get(svar.lower())
    if not svar:
        raise ValueError("Svar må være 'har' eller 'kjop'.")
    s = lagermod.stamme(vare)
    for x in list(plan.get("ekstra", [])):
        if x.get("sjekk") and s and (s in lagermod.stamme(x["navn"]) or lagermod.stamme(x["navn"]) in s):
            if svar == "har":
                plan["ekstra"].remove(x)
            else:
                x.pop("sjekk")
            _endret(plan)
            lagre(plan)
            return {"tittel": x["navn"]}
    liste = handleliste_for(oda, plan, p)
    nokkel, linje = handleliste.finn_nokkel(liste, vare)
    if not nokkel:
        raise ValueError(f"Fant ikke '{vare}' i ukens ingredienser.")
    plan["avklaringer"][nokkel] = svar
    # Langholdbare varer vi har, huskes i lageret så vi ikke spør igjen neste uke
    if svar == "har" and linje.get("holdbar_dager", 0) >= 30:
        lagermod.legg_til(linje["tittel"])
    _endret(plan)
    lagre(plan)
    return linje


def erstatt(oda, plan: dict, p: dict, vare: str, produkt_id: int, navn: str = None, pris: float = 0.0) -> dict:
    liste = handleliste_for(oda, plan, p)
    nokkel, linje = handleliste.finn_nokkel(liste, vare)
    if not nokkel:
        raise ValueError(f"Fant ikke '{vare}' i ukens ingredienser.")
    navn = navn or linje["tittel"]
    plan["produktvalg"][nokkel] = {"id": int(produkt_id), "name": navn, "full_name": navn, "name_extra": navn, "price": pris, "available": True}
    _endret(plan)
    lagre(plan)
    return linje


def ekstra(plan: dict, produkt_id: int, navn: str, antall: int = 1, pris: float = 0.0) -> None:
    """Vare utenom rettene, f.eks. kyllingbuljong til skapet."""
    liste = plan.setdefault("ekstra", [])
    liste[:] = [x for x in liste if x["id"] != int(produkt_id)]
    liste.append({"id": int(produkt_id), "navn": navn, "antall": int(antall), "pris": float(pris)})
    _endret(plan)
    lagre(plan)


def fjern_ekstra(plan: dict, produkt_id: int) -> bool:
    liste = plan.get("ekstra", [])
    for i, x in enumerate(liste):
        if x["id"] == int(produkt_id):
            liste.pop(i)
            _endret(plan)
            lagre(plan)
            return True
    return False


def _ekstralinje(x: dict) -> dict:
    produkt = {"id": x["id"], "name": x["navn"], "full_name": x["navn"], "name_extra": "", "price": x.get("pris", 0), "available": True}
    return {"nokkel": f"ekstra:{x['id']}", "tittel": x["navn"], "produkt": produkt, "antall": x["antall"], "ekstra": True}


# --- til kurv ---

def kurvplan(oda, plan: dict, p: dict, trekk_fra_kurv: bool = False) -> dict:
    """Hva som ville blitt lagt i kurven nå. Endrer ingenting."""
    liste = handleliste_for(oda, plan, p)
    oda.sikre_innlogget()
    kurv = oda.kurv()
    i_kurv = {x["id"]: x["quantity"] for x in kurv.get("items", [])}
    allerede_lagt = {x["id"] for logg in plan.get("kurvlogg", []) for x in logg["lagt"]} if plan["status"] == "delvis_i_kurv" else set()
    linjer, hoppet_over = [], []
    for x in liste["kjop"] + [_ekstralinje(e) for e in plan.get("ekstra", []) if not e.get("sjekk")]:
        pid = x["produkt"]["id"]
        antall = x["antall"]
        if pid in allerede_lagt:
            hoppet_over.append((x, "lagt i kurven ved forrige forsøk"))
            continue
        if not x.get("tilgjengelig", True):
            hoppet_over.append((x, "utsolgt"))
            continue
        allerede = i_kurv.get(pid, 0)
        if trekk_fra_kurv and allerede:
            antall = max(0, antall - allerede)
            if antall == 0:
                hoppet_over.append((x, f"ligger allerede {allerede} i kurven"))
                continue
        linjer.append({"linje": x, "antall": antall, "allerede_i_kurv": allerede})
    return {"liste": liste, "kurv_for": kurv, "linjer": linjer, "hoppet_over": hoppet_over}


def til_kurv(oda, plan: dict, p: dict, trekk_fra_kurv: bool = False) -> dict:
    if plan["status"] == "i_kurv":
        raise ValueError("Planen er allerede lagt i kurven. Lag en ny plan, eller bruk plan gjenapne hvis du vil legge i kurven på nytt.")
    if plan["status"] == "delvis_i_kurv":
        if plan.get("godkjent_hash") != _hash(plan):
            raise ValueError("Planen er endret etter forrige forsøk. Kjør plan gjenapne og godkjenn på nytt.")
    elif not er_godkjent(plan):
        raise ValueError("Planen er ikke godkjent (eller endret etter godkjenning). Vis planen for Ole og kjør plan godkjenn først.")
    kp = kurvplan(oda, plan, p, trekk_fra_kurv)
    if kp["liste"]["usikre"]:
        raise ValueError("Det finnes uavklarte varer. Kjør plan vis.")
    lagt, feil = [], []
    for l in kp["linjer"]:
        try:
            oda.legg_i_kurv(l["linje"]["produkt"]["id"], l["antall"])
            lagt.append(l)
        except OdaFeil as e:
            feil.append((l, str(e)))
    etter = oda.kurv()
    if lagt:
        plan["status"] = "delvis_i_kurv" if feil else "i_kurv"
    plan["kurvlogg"].append({"tid": dt.datetime.now().isoformat(timespec="minutes"),
                             "lagt": [{"id": l["linje"]["produkt"]["id"], "antall": l["antall"]} for l in lagt],
                             "feil": [{"id": l["linje"]["produkt"]["id"], "feil": f} for l, f in feil]})
    lagre(plan)
    if lagt:
        _lagre_historikk(plan)
    return kp | {"lagt": lagt, "feil": feil, "kurv_etter": etter}


def gjenapne(plan: dict) -> None:
    plan["status"] = "utkast"
    plan["godkjent_hash"] = None
    lagre(plan)


# --- etter uken ---

def ferdig(oda, plan: dict, p: dict) -> list:
    """Uken er over: trekk fra det rettene brukte av lageret, registrer rester og fryste middager."""
    if plan["status"] == "ferdig":
        raise ValueError(f"Uke {plan['uke']} er allerede avsluttet.")
    liste = handleliste_for(oda, plan, p)
    meldinger = []
    # 1. Det rettene tok fra lageret
    for x in liste["fra_lager"]:
        if not x.get("lagervare"):
            continue
        dim, mengde = x["behov_basis"] if x.get("behov_basis") else (None, None)
        m = lagermod.brukt(x["lagervare"], dim, mengde, ", ".join(x["retter"]), plan["uke"])
        if m:
            meldinger.append(m)
    for x in liste["kjop"]:
        if x.get("delvis_lager") and x.get("lagervare") and lagermod.fjern(x["lagervare"]):
            meldinger.append(f"{x['lagervare']}: brukt opp")
    # 2. Rester av det vi kjøpte
    for x in liste["kjop"]:
        if x["rest_pakker"] < handleliste.REST_VARSEL:
            continue
        storrelse = handleliste._produkt_storrelse(x["produkt"])
        if storrelse:
            mengde = enheter.vis(storrelse[0], storrelse[1] * x["rest_pakker"])
        else:
            mengde = f"ca {round(x['rest_pakker'] * 100)} % av pakken"
        lagermod.legg_til(x["tittel"], mengde=mengde, notat=f"rest fra {plan['uke']}, sjekk dato" if x["fersk"] else f"rest fra {plan['uke']}")
        meldinger.append(f"{x['tittel']}: {mengde}")
    for d in plan["dager"]:
        if d["type"] == "ferdigmiddag":
            lagermod.bruk_ferdigmiddag(d["navn"], d["porsjoner"])
            meldinger.append(f"Brukt fra fryseren: {d['navn']}")
        if d["type"] == "lag_dobbel" and d.get("frys_til"):
            lagermod.legg_til(d["navn"], fryst_middag_porsjoner=d["porsjoner"], notat=f"laget {d['dato']}")
            meldinger.append(f"I fryseren: {d['navn']} ({d['porsjoner']} porsjoner)")
    for x in plan.get("ekstra_middager", []):
        lagermod.legg_til(x["navn"], fryst_middag_porsjoner=x["porsjoner"], sjekk=True,
                          notat=f"ekstra middag fra {plan['uke']} – bekreft at den ble laget og fryst (lager ok)")
        meldinger.append(f"Ekstra middag {x['navn']} ({x['porsjoner']} porsjoner): bekreft at den ligger i fryseren med «lager ok {x['navn']}»")
    plan["status"] = "ferdig"
    lagre(plan)
    return meldinger
