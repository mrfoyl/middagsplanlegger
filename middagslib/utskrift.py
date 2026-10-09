"""Skriv ut dagens oppskrift på skriveren (CUPS via `lp`).

Tenkt kjørt fra cron på openclaw hver morgen: finner dagens rett i planen,
lager en A4-PDF med ingredienser skalert til porsjonene som skal lages (dobbelt
på dobbeldager), fremgangsmåte, produktbytter og påminnelse om å ta ut fra
fryseren. Hver dag skrives bare ut én gang, med mindre man ber om det.
"""
import datetime as dt
import re
import shutil
import subprocess
import time
from pathlib import Path

from . import handleliste, lagring, oppskrifter, profil as profilmod
from .planlegger import lagre

UTSKRIVBARE_STATUSER = ("godkjent", "i_kurv", "delvis_i_kurv", "ferdig")


def _detaljer(oda, ref: str) -> dict:
    """Beskrivelse og fremgangsmåte. Oda-oppskrifter caches som ingrediensene."""
    if ref.startswith("egen:"):
        e = oppskrifter.egne().get(ref[5:], {})
        tekst = e.get("instruksjoner") or ""
        steg = [s.strip() for s in re.split(r"\n+|(?<=[.!])\s+(?=\d+\.)", tekst) if s.strip()]
        return {"description": "", "instructions": steg}
    oid = int(ref[4:])
    navn = f"cache/detaljer_{oid}.json"
    c = lagring.les(navn, None)
    if c and time.time() - c.get("_hentet", 0) < oppskrifter.CACHE_DAGER * 86400:
        return c
    data = oda.oppskrift_detaljer(oid)
    data["_hentet"] = time.time()
    lagring.skriv(navn, data)
    return data


def _mengde(tall: float, enhet: str) -> str:
    if not tall:
        return ""
    t = round(tall, 1)
    return f"{t:g} {enhet}".strip()


def innhold(oda, plan: dict, dag: dict) -> dict:
    """Alt som skal på arket, som rene data (testbart uten PDF)."""
    r = oppskrifter.hent(oda, dag["ref"])
    porsjoner = dag["porsjoner"] * (2 if dag["type"] == "lag_dobbel" else 1)
    faktor = porsjoner / max(1, r["standard_porsjoner"])
    grupper = {}
    for i in r["ingredienser"]:
        linje = f"{_mengde(i.get('display_quantity', 0) * faktor, i.get('display_unit', ''))} {i['title']}".strip()
        grupper.setdefault(i.get("group") or "", []).append(linje)

    bytter = []
    nokler = {handleliste.nokkel(i) for i in r["ingredienser"]}
    for nokkel, b in plan.get("bytter", {}).items():
        if nokkel in nokler:
            bytter.append(f"{b['tittel']}: vi kjøpte {b['til']} (oppskriften nevner {b['fra']})")

    merknader = []
    if dag["type"] == "lag_dobbel":
        if dag.get("frys_til"):
            merknader.append(f"Lag dobbel porsjon. Frys ned halvparten til {dag['frys_til']} neste uke.")
        else:
            merknader.append(f"Lag dobbel porsjon. Halvparten er middag {dag['rest_til']}.")
    i_morgen = (dt.date.fromisoformat(dag["dato"]) + dt.timedelta(days=1)).isoformat()
    for d in plan["dager"]:
        if d["dato"] == i_morgen and (d["type"] == "ferdigmiddag" or (d["type"] == "rest" and d.get("lagring") == "fryser")):
            merknader.append(f"Husk: ta ut {d['navn']} fra fryseren i kveld til i morgen.")

    detaljer = _detaljer(oda, dag["ref"])
    return {
        "tittel": r["navn"],
        "undertittel": f"{dag['dag'].capitalize()} {dag['dato']} · {porsjoner} porsjoner · {r.get('minutter') or '?'} min",
        "beskrivelse": detaljer.get("description", ""),
        "ingredienser": grupper,
        "steg": [s for s in detaljer.get("instructions", []) if s],
        "skalering": f"Oppskriften er skrevet for {r['standard_porsjoner']} porsjoner – mengdene under er regnet om til {porsjoner}. "
                     f"Mengder i fremgangsmåten gjelder {r['standard_porsjoner']} porsjoner." if porsjoner != r["standard_porsjoner"] else "",
        "bytter": bytter,
        "merknader": merknader,
        "url": r.get("url", ""),
    }


def lag_pdf(data: dict, sti: Path) -> Path:
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import cm
    from reportlab.platypus import ListFlowable, ListItem, Paragraph, SimpleDocTemplate, Spacer
    from xml.sax.saxutils import escape

    s = getSampleStyleSheet()
    tittel = ParagraphStyle("t", parent=s["Title"], fontSize=20, spaceAfter=4, alignment=0)
    under = ParagraphStyle("u", parent=s["Normal"], fontSize=10, textColor="#555555", spaceAfter=10)
    h = ParagraphStyle("h", parent=s["Heading3"], spaceBefore=8, spaceAfter=4)
    tekst = ParagraphStyle("b", parent=s["Normal"], fontSize=11, leading=15)
    liten = ParagraphStyle("l", parent=s["Normal"], fontSize=9, leading=12, textColor="#555555")
    merk = ParagraphStyle("m", parent=tekst, backColor="#eeeeee", borderPadding=6, spaceBefore=4, spaceAfter=8)

    e = lambda x: escape(str(x))
    flyt = [Paragraph(e(data["tittel"]), tittel), Paragraph(e(data["undertittel"]), under)]
    for m in data["merknader"]:
        flyt.append(Paragraph("<b>" + e(m) + "</b>", merk))
    if data["beskrivelse"]:
        flyt.append(Paragraph(e(data["beskrivelse"]), tekst))
    flyt.append(Paragraph("Ingredienser", h))
    for gruppe, linjer in data["ingredienser"].items():
        if gruppe and len(data["ingredienser"]) > 1:
            flyt.append(Paragraph("<i>" + e(gruppe) + "</i>", tekst))
        flyt.append(ListFlowable([ListItem(Paragraph(e(l), tekst)) for l in linjer], bulletType="bullet", leftIndent=12))
    if data["skalering"]:
        flyt.append(Paragraph(e(data["skalering"]), liten))
    if data["bytter"]:
        flyt.append(Paragraph("Produktbytter", h))
        flyt += [Paragraph(e(b), liten) for b in data["bytter"]]
    if data["steg"]:
        flyt.append(Paragraph("Slik gjør du", h))
        flyt.append(ListFlowable([ListItem(Paragraph(e(st), tekst), spaceAfter=4) for st in data["steg"]], bulletType="1", leftIndent=14))
    if data["url"]:
        flyt.append(Spacer(1, 10))
        flyt.append(Paragraph(e(data["url"]), liten))

    SimpleDocTemplate(str(sti), pagesize=A4, leftMargin=2 * cm, rightMargin=2 * cm, topMargin=1.8 * cm, bottomMargin=1.8 * cm,
                      title=data["tittel"]).build(flyt)
    return sti


def lag_tekst(data: dict, sti: Path) -> Path:
    """Reserve når reportlab mangler: ren tekst, som CUPS også skriver ut."""
    linjer = [data["tittel"].upper(), data["undertittel"], ""]
    linjer += [f"!! {m}" for m in data["merknader"]] + ([""] if data["merknader"] else [])
    if data["beskrivelse"]:
        linjer += [data["beskrivelse"], ""]
    linjer.append("INGREDIENSER")
    for gruppe, ls in data["ingredienser"].items():
        if gruppe and len(data["ingredienser"]) > 1:
            linjer.append(f"  {gruppe}:")
        linjer += [f"  - {l}" for l in ls]
    if data["skalering"]:
        linjer += ["", data["skalering"]]
    if data["bytter"]:
        linjer += ["", "PRODUKTBYTTER"] + [f"  {b}" for b in data["bytter"]]
    if data["steg"]:
        linjer += ["", "SLIK GJØR DU"] + [f"  {n}. {st}" for n, st in enumerate(data["steg"], 1)]
    if data["url"]:
        linjer += ["", data["url"]]
    sti.write_text("\n".join(linjer) + "\n", encoding="utf-8")
    return sti


def finn_dag(plan: dict, dato: dt.date):
    for d in plan["dager"]:
        if d["dato"] == dato.isoformat():
            return d
    return None


def skriv_ut(oda, plan: dict, dato: dt.date, skriver: str = None, bare_fil: bool = False, igjen: bool = False) -> str:
    """Skriv ut oppskriften for `dato`. Returnerer en melding om hva som skjedde."""
    if plan["status"] not in UTSKRIVBARE_STATUSER:
        return f"Planen for {plan['uke']} er ikke godkjent ennå – skriver ikke ut."
    d = finn_dag(plan, dato)
    if not d:
        return f"Ingen middag planlagt {dato.isoformat()} i planen for {plan['uke']}."
    if d["type"] not in ("lag", "lag_dobbel") or not d.get("ref"):
        return f"{d['dag']} {d['dato']}: {d['type']} – ingen oppskrift å skrive ut."
    utskrevet = plan.setdefault("utskrevet", [])
    if d["dato"] in utskrevet and not igjen and not bare_fil:
        return f"Oppskriften for {d['dato']} er allerede skrevet ut (bruk --igjen for å skrive ut på nytt)."

    ark = [innhold(oda, plan, d)]
    for x in plan.get("ekstra_middager", []):
        if x.get("lagedag") == d["dag"]:
            ekstra = innhold(oda, plan, {"dag": d["dag"], "dato": d["dato"], "type": "lag", "ref": x["ref"], "porsjoner": x["porsjoner"]})
            ekstra["merknader"] = ["Ekstra middag til fryseren: lag denne i tillegg i dag og frys den ned."]
            ark.append(ekstra)
    katalog = lagring.datakatalog() / "utskrift"
    katalog.mkdir(exist_ok=True)
    filer = []
    for data in ark:
        navn = f"{d['dato']}-{oppskrifter.slug(data['tittel'])}"
        try:
            filer.append(lag_pdf(data, katalog / f"{navn}.pdf"))
        except ImportError:
            filer.append(lag_tekst(data, katalog / f"{navn}.txt"))
    if bare_fil:
        return "Laget " + ", ".join(str(f) for f in filer) + " (ikke skrevet ut)."

    skriver = skriver or profilmod.last().get("skriver")
    if not shutil.which("lp"):
        raise RuntimeError(f"Fant ikke `lp` (CUPS). Filen ligger i {fil}.")
    jobber = []
    for data, fil in zip(ark, filer):
        args = ["lp"] + (["-d", skriver] if skriver else []) + ["-t", data["tittel"], str(fil)]
        r = subprocess.run(args, capture_output=True, text=True, timeout=60)
        if r.returncode != 0:
            raise RuntimeError(f"Utskrift feilet for «{data['tittel']}»: {(r.stderr or r.stdout).strip()}")
        jobber.append(f"«{data['tittel']}»")
    utskrevet.append(d["dato"])
    lagre(plan)
    return f"Skrev ut {' og '.join(jobber)} på {skriver or 'standardskriveren'}."
