#!/usr/bin/env python3
"""Familie-middagsplanlegger som fyller handlekurven på Oda – men aldri bestiller.

Kalles av agenten (Kølla) på samme måte som listonic.py. Kjør uten argumenter
eller med -h for oversikt. Se README.md for flyten.
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from middagslib import lager, oppskrifter, planlegger, profil, rapport  # noqa: E402
from middagslib.oda import Oda, OdaFeil  # noqa: E402


def _oda():
    return Oda()


def _vis_plan(oda, plan, p):
    print(rapport.plan(plan, planlegger.handleliste_for(oda, plan, p)))


# --- profil ---

def cmd_profil(a):
    if a.handling == "vis":
        p = profil.last()
    elif a.handling == "sett":
        p = profil.sett(a.felt, " ".join(a.verdi))
    elif a.handling == "legg-til":
        p = profil.legg_til(a.felt, a.verdi)
    elif a.handling == "fjern":
        p = profil.fjern(a.felt, a.verdi)
    elif a.handling == "medlem-legg-til":
        p = profil.legg_til_medlem(a.felt, int(a.verdi[0]))
    elif a.handling == "medlem-fjern":
        p = profil.fjern_medlem(int(a.felt))
    elif a.handling == "medlem-alder":
        p = profil.sett_alder(int(a.felt), int(a.verdi[0]))
    print(profil.tekst(p))


# --- lager ---

def cmd_lager(a):
    if a.handling == "vis":
        pass
    elif a.handling == "legg-til":
        for vare in a.varer:
            navn, _, mengde = vare.partition("=")
            v = lager.legg_til(navn, mengde.strip() or a.mengde, a.fryst_middag)
            print(f"La til: {v['navn']}" + (f" ({v['mengde']})" if v.get("mengde") else "") + (f" – {v['porsjoner']} porsjoner i fryseren" if v.get("fryst_middag") else ""))
    elif a.handling == "fjern":
        for vare in a.varer:
            print(f"Fjernet: {vare}" if lager.fjern(vare) else f"Fant ikke: {vare}")
    elif a.handling == "tom":
        print(f"Tømte lageret ({lager.tom()} varer).")
    print(lager.tekst())


# --- oda ---

def cmd_oda(a):
    oda = _oda()
    if a.handling == "login":
        oda.logg_inn()
        print("Logget inn hos Oda. " + oda.bruker())
    elif a.handling == "status":
        print(oda.bruker())


def cmd_produkt(a):
    res = _oda().sok_produkt(" ".join(a.sok))
    for x in res.get("items", [])[: a.antall]:
        tilgj = "" if x["availability"]["is_available"] else " (UTSOLGT)"
        print(f"{x['id']}: {x['name']} – {x['subtitle']} – {x['price']:.2f} kr{tilgj}")


# --- oppskrifter ---

def cmd_oppskrift(a):
    if a.handling == "sok":
        res = _oda().sok_oppskrift(" ".join(a.args) or None)
        for x in res.get("items", [])[:15]:
            print(f"oda:{x['id']}: {x['name']} ({x.get('duration') or '?'})")
        return
    if a.handling == "vis":
        r = oppskrifter.hent(_oda(), oppskrifter.normaliser_ref(a.args[0]))
        p = profil.last()
        ok, grunn = planlegger.egnet(r, p)
        print(f"*{r['navn']}* [{r['ref']}] – {r['minutter'] or '?'} min, {r['standard_porsjoner']} porsjoner, {'frysbar' if r['frysbar'] else 'ikke frysbar'}")
        if r.get("url"):
            print(r["url"])
        if not ok:
            print(f"⚠️ Passer ikke familien: {grunn}")
        for i in r["ingredienser"]:
            mengde = f"{i['display_quantity']:g} {i['display_unit']} " if i.get("display_quantity") else ""
            prod = f" → {i['product']['full_name']} [{i['product']['id']}]" if i.get("product") else ""
            print(f"  • {mengde}{i['title']}{' (basis)' if i.get('is_basic') else ''}{prod}")
        return
    if a.handling == "frysbar":
        oppskrifter.sett_frysbar(oppskrifter.normaliser_ref(a.args[0]), a.args[1].lower() in ("ja", "true", "1"))
        print("Lagret.")
        return
    if a.handling == "egne":
        for s, e in oppskrifter.egne().items():
            print(f"egen:{s}: {e['navn']} ({e['porsjoner']} porsj.{', frysbar' if e.get('frysbar') else ''}{', lagret i Oda' if e.get('oda_liste_id') else ''})")
        return
    if a.handling == "egen-synk":
        e = oppskrifter.synk_egen(_oda(), oppskrifter.normaliser_ref(a.args[0])[5:])
        print(f"Lagret «{e['navn']}» i Oda under Oppskrifter → Dine middager (liste {e['oda_liste_id']}).")


def cmd_egen(a):
    frysbar = True if a.frysbar else (False if a.ikke_frysbar else None)
    e = oppskrifter.lag_egen(a.navn, a.porsjoner, a.ingrediens, a.instruksjoner or "", a.minutter, frysbar)
    print(f"Lagret egen oppskrift egen:{e['slug']} – {e['navn']} ({len(e['ingredienser'])} ingredienser{', frysbar' if e['frysbar'] else ''}).")
    print(f"Lagre den i Oda også med: middag.py oppskrift egen-synk egen:{e['slug']}")


# --- plan ---

def cmd_plan(a):
    oda = _oda()
    p = profil.last()
    h = a.handling

    if h == "lag":
        uke = a.uke or planlegger.neste_uke()
        try:
            forrige = planlegger.last()
        except ValueError:
            forrige = None
        if forrige and forrige["uke"] < uke and forrige["status"] in ("i_kurv", "delvis_i_kurv"):
            meldinger = planlegger.ferdig(oda, forrige, p)
            print(f"Avsluttet {forrige['uke']}." + (" " + "; ".join(meldinger) if meldinger else ""))
        ekstra = [profil.normaliser_dag(d) for d in (a.aktivitet or "").split(",") if d.strip()]
        kalenderdager = set()
        if p["kalender"]["aktiv"]:
            from middagslib import kalender
            kalenderdager, melding = kalender.aktivitetsdager(uke, p["kalender"]["sokeord"])
            print(melding)
        onsket = [r for r in (a.oppskrifter or "").split(",") if r.strip()]
        plan = planlegger.lag(oda, p, uke, ekstra, kalenderdager, onsket, a.maks_kandidater, logg=lambda m: print(m, file=sys.stderr))
        _vis_plan(oda, plan, p)
        return

    plan = planlegger.last()
    if h == "vis":
        _vis_plan(oda, plan, p)
    elif h == "json":
        liste = planlegger.handleliste_for(oda, plan, p)
        print(json.dumps({"plan": plan, "handleliste": liste}, ensure_ascii=False, indent=1))
    elif h == "bytt":
        for m in planlegger.bytt(oda, plan, a.args[0], a.args[1]):
            print(m)
        _vis_plan(oda, plan, p)
    elif h == "dobbel":
        planlegger.dobbel(plan, a.args[0], a.args[1])
        _vis_plan(oda, plan, p)
    elif h == "fri":
        planlegger.fri(plan, a.args[0])
        _vis_plan(oda, plan, p)
    elif h == "ferdigmiddag":
        planlegger.ferdigmiddag(plan, a.args[0], " ".join(a.args[1:]))
        _vis_plan(oda, plan, p)
    elif h == "avklar":
        *vare, svar = a.args
        linje = planlegger.avklar(oda, plan, p, " ".join(vare), svar)
        print(f"Notert: {linje['tittel']} → {svar}")
        print(rapport.handleliste(planlegger.handleliste_for(oda, plan, p)))
    elif h == "erstatt":
        *vare, pid = a.args
        linje = planlegger.erstatt(oda, plan, p, " ".join(vare), int(pid), a.navn, a.pris or 0.0)
        print(f"{linje['tittel']} → produkt {pid}")
        print(rapport.handleliste(planlegger.handleliste_for(oda, plan, p)))
    elif h == "godkjenn":
        planlegger.godkjenn(oda, plan, p)
        print("Planen er godkjent.")
        print(rapport.kurv_for(planlegger.kurvplan(oda, plan, p, a.trekk_fra_kurv)))
        print("Si «legg i kurven» så kjører jeg: plan kurv --utfor")
    elif h == "kurv":
        if a.utfor:
            print(rapport.kurv_etter(planlegger.til_kurv(oda, plan, p, a.trekk_fra_kurv)))
        else:
            if not planlegger.er_godkjent(plan):
                print("(Planen er ikke godkjent ennå – dette er bare en forhåndsvisning.)")
            print(rapport.kurv_for(planlegger.kurvplan(oda, plan, p, a.trekk_fra_kurv)))
    elif h == "gjenapne":
        planlegger.gjenapne(plan)
        print("Planen er åpnet igjen som utkast.")
    elif h == "ferdig":
        meldinger = planlegger.ferdig(oda, plan, p)
        print("Uken er avsluttet." + (" Lagt i lageret: " + "; ".join(meldinger) if meldinger else ""))


def parser():
    ap = argparse.ArgumentParser(prog="middag.py", description="Familie-middagsplanlegger for Oda. Bestiller aldri – fyller bare kurven.")
    sub = ap.add_subparsers(dest="kommando", required=True)

    sp = sub.add_parser("profil", help="familieprofil")
    sp.add_argument("handling", choices=["vis", "sett", "legg-til", "fjern", "medlem-legg-til", "medlem-fjern", "medlem-alder"])
    sp.add_argument("felt", nargs="?")
    sp.add_argument("verdi", nargs="*")
    sp.set_defaults(func=cmd_profil)

    sl = sub.add_parser("lager", help="det vi har hjemme")
    sl.add_argument("handling", choices=["vis", "legg-til", "fjern", "tom"])
    sl.add_argument("varer", nargs="*", help='"vare" eller "vare=mengde", f.eks. "melk=1 l"')
    sl.add_argument("--mengde")
    sl.add_argument("--fryst-middag", type=int, metavar="PORSJONER", help="ferdigmiddag i fryseren")
    sl.set_defaults(func=cmd_lager)

    so = sub.add_parser("oda", help="innlogging")
    so.add_argument("handling", choices=["login", "status"])
    so.set_defaults(func=cmd_oda)

    spr = sub.add_parser("produkt", help="søk etter produkter hos Oda")
    spr.add_argument("handling", choices=["sok"])
    spr.add_argument("sok", nargs="+")
    spr.add_argument("--antall", type=int, default=10)
    spr.set_defaults(func=cmd_produkt)

    sop = sub.add_parser("oppskrift", help="oppskrifter")
    sop.add_argument("handling", choices=["sok", "vis", "frysbar", "egne", "egen-synk"])
    sop.add_argument("args", nargs="*")
    sop.set_defaults(func=cmd_oppskrift)

    se = sub.add_parser("egen", help="lag/oppdater egen oppskrift")
    se.add_argument("navn")
    se.add_argument("--porsjoner", type=int, default=4)
    se.add_argument("--ingrediens", action="append", default=[], help='"Kjøttdeig=12345:1" (produkt-ID:pakker for hele oppskriften), eller "Salt" for basisvare')
    se.add_argument("--instruksjoner")
    se.add_argument("--minutter", type=int)
    se.add_argument("--frysbar", action="store_true")
    se.add_argument("--ikke-frysbar", action="store_true")
    se.set_defaults(func=cmd_egen)

    spl = sub.add_parser("plan", help="ukesplan")
    spl.add_argument("handling", choices=["lag", "vis", "json", "bytt", "dobbel", "fri", "ferdigmiddag", "avklar", "erstatt", "godkjenn", "kurv", "gjenapne", "ferdig"])
    spl.add_argument("args", nargs="*")
    spl.add_argument("--uke", help="f.eks. 2026-W42 (standard: neste uke)")
    spl.add_argument("--aktivitet", help="ekstra aktivitetsdager denne uken, f.eks. tir,tor")
    spl.add_argument("--oppskrifter", help="ønskede retter, f.eks. oda:3004,egen:taco")
    spl.add_argument("--maks-kandidater", type=int, default=30)
    spl.add_argument("--utfor", action="store_true", help="legg faktisk i kurven (krever godkjent plan)")
    spl.add_argument("--trekk-fra-kurv", action="store_true", help="ikke legg til det som allerede ligger i kurven")
    spl.add_argument("--navn")
    spl.add_argument("--pris", type=float)
    spl.set_defaults(func=cmd_plan)
    return ap


def main(argv=None):
    a = parser().parse_args(argv)
    try:
        a.func(a)
    except (ValueError, OdaFeil) as e:
        print(f"Feil: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
