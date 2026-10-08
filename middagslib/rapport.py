"""Tekst for WhatsApp: *fet* skrift, korte linjer, norsk."""
import datetime as dt

STATUS = {"utkast": "utkast – ikke godkjent", "godkjent": "godkjent – klar for kurv", "i_kurv": "lagt i Oda-kurven", "delvis_i_kurv": "delvis lagt i kurven – noe feilet", "ferdig": "ferdig"}


def _kr(x: float) -> str:
    return f"{x:,.0f} kr".replace(",", " ")


def _dato(iso: str) -> str:
    d = dt.date.fromisoformat(iso)
    return f"{d.day}.{d.month}"


def _produktnavn(x: dict) -> str:
    p = x.get("produkt") or {}
    navn = p.get("full_name") or p.get("name") or x["tittel"]
    extra = p.get("name_extra", "")
    storrelse = extra.split(",")[-1].strip() if extra else ""
    return f"{navn} ({storrelse})" if storrelse and storrelse not in navn else navn


def dagslinje(d: dict) -> str:
    hode = f"{d['dag']} {_dato(d['dato'])}" + (" 🏃" if d.get("aktivitet") else "")
    t = d["type"]
    tid = f", {d['minutter']} min" if d.get("minutter") else ""
    if t == "lag":
        return f"{hode}: {d['navn']}{tid} [{d['ref']}]"
    if t == "lag_dobbel" and d.get("frys_til"):
        return f"{hode}: {d['navn']}{tid} – *lag dobbel*, frys ned til {d['frys_til']} neste uke [{d['ref']}]"
    if t == "lag_dobbel":
        return f"{hode}: {d['navn']}{tid} – *lag dobbel*, rest til {d['rest_til']} [{d['ref']}]"
    if t == "rest":
        hvor = "fra fryseren – ta ut kvelden før" if d.get("lagring") == "fryser" else "fra kjøleskapet"
        return f"{hode}: Rest av {d['navn']} ({hvor}, laget {d['fra_dag']})"
    if t == "ferdigmiddag":
        return f"{hode}: {d['navn']} fra fryseren"
    if t == "fri":
        return f"{hode}: (ingen middag planlagt)"
    return f"{hode}: ❗ mangler rett"


def plan(plan_: dict, liste: dict) -> str:
    dager = plan_["dager"]
    linjer = [f"*Middagsplan {plan_['uke']}* ({_dato(dager[0]['dato'])}–{_dato(dager[-1]['dato'])}), {plan_['porsjoner']} porsjoner – {STATUS.get(plan_['status'], plan_['status'])}"]
    linjer += [dagslinje(d) for d in dager]
    linjer.append("")
    linjer.append(handleliste(liste))
    if plan_.get("ekstra"):
        linjer.append("*Ekstra (utenom rettene)*")
        linjer += [f"  {x['antall']}× {x['navn']}" for x in plan_["ekstra"]]
    if plan_.get("advarsler"):
        linjer.append("")
        linjer.append("*Merk*")
        linjer += [f"  ⚠️ {a}" for a in plan_["advarsler"]]
    linjer.append("")
    if liste["usikre"]:
        linjer.append("Neste steg: svar på spørsmålene over (plan avklar <vare> har|kjop).")
    elif plan_["status"] == "utkast":
        linjer.append("Neste steg: ser planen bra ut? Da godkjenner jeg den og viser hva som legges i kurven.")
    return "\n".join(linjer)


def handleliste(liste: dict) -> str:
    linjer = [f"*Handleliste* ({len(liste['kjop'])} varer, ca {_kr(liste['sum'])})"]
    ferske = [x for x in liste["kjop"] if x["fersk"]]
    andre = [x for x in liste["kjop"] if not x["fersk"]]
    for tittel, gruppe in (("Ferskvare", ferske), ("Annet", andre)):
        if not gruppe:
            continue
        linjer.append(f"_{tittel}_")
        for x in gruppe:
            ekstra = []
            if len(x["retter"]) > 1:
                ekstra.append(f"brukes i {len(x['retter'])} retter")
            if x.get("delvis_lager"):
                ekstra.append(x["delvis_lager"])
            if not x.get("tilgjengelig", True):
                ekstra.append("UTSOLGT")
            linjer.append(f"  {x['antall']}× {_produktnavn(x)}" + (f" – {', '.join(ekstra)}" if ekstra else ""))
    if liste["uten_produkt"]:
        linjer.append("_Mangler produkt hos Oda_")
        linjer += [f"  • {x['tittel']} (finn med: produkt sok, og sett med plan erstatt)" for x in liste["uten_produkt"]]
    if liste["fra_lager"]:
        linjer.append("*Fra lager*: " + "; ".join(f"{x['tittel']} ({x['merknad']})" for x in liste["fra_lager"]))
    if liste["basis_hjemme"]:
        linjer.append("*Antatt hjemme*: " + ", ".join(x["tittel"] for x in liste["basis_hjemme"]))
    if liste["rester"]:
        linjer.append(f"*Ferskvare til overs* (ca {_kr(liste['svinn'])}):")
        for r in sorted(liste["rester"], key=lambda r: -r["verdi"]):
            linjer.append(f"  • {r['tittel']}: ~{round(r['rest_pakker'] * 100)} % av pakken")
    if liste["usikre"]:
        linjer.append("*❓ Må avklares* (svar «har» eller «kjøp»):")
        for u in liste["usikre"]:
            linjer.append(f"  • {u['tittel']} – {u['grunn']}")
    return "\n".join(linjer)


def kurv_for(kp: dict) -> str:
    linjer = ["*Dette legges i Oda-kurven* (ingenting er lagt til ennå)"]
    total = 0.0
    for l in kp["linjer"]:
        x = l["linje"]
        pris = (x["produkt"].get("price") or 0) * l["antall"]
        total += pris
        merk = f" (ligger allerede {l['allerede_i_kurv']} i kurven)" if l["allerede_i_kurv"] else ""
        linjer.append(f"  {l['antall']}× {_produktnavn(x)}{merk}")
    linjer.append(f"Ca {_kr(total)} for middagsvarene.")
    if kp["hoppet_over"]:
        linjer.append("*Hoppes over*: " + "; ".join(f"{x['tittel']} ({grunn})" for x, grunn in kp["hoppet_over"]))
    kurv = kp["kurv_for"]
    linjer.append(f"Kurven har nå {kurv.get('product_quantity_count', len(kurv.get('items', [])))} varer for {_kr(kurv.get('total_gross_amount') or 0)}.")
    if any(l["allerede_i_kurv"] for l in kp["linjer"]):
        linjer.append("Noen varer ligger allerede i kurven. Si fra om jeg skal trekke dem fra (--trekk-fra-kurv).")
    linjer.append("Jeg bestiller aldri – du trykker «bestill» selv i Oda.")
    return "\n".join(linjer)


def kurv_etter(res: dict) -> str:
    linjer = [f"*Lagt i Oda-kurven*: {len(res['lagt'])} varer"]
    if res["feil"]:
        linjer.append("*Feilet*:")
        linjer += [f"  • {_produktnavn(l['linje'])}: {f}" for l, f in res["feil"]]
    if res["hoppet_over"]:
        linjer.append("*Hoppet over*: " + "; ".join(f"{x['tittel']} ({grunn})" for x, grunn in res["hoppet_over"]))
    for navn, kurv in (("Før", res["kurv_for"]), ("Etter", res["kurv_etter"])):
        linjer.append(f"{navn}: {kurv.get('product_quantity_count', '?')} varer, {_kr(kurv.get('total_gross_amount') or 0)}")
    if res["feil"] and res["lagt"]:
        linjer.append("Kjør plan kurv --utfor igjen for å prøve de feilede på nytt (det som alt er lagt til, hoppes over).")
    elif res["feil"]:
        linjer.append("Ingenting ble lagt til. Planen er fortsatt godkjent – rett feilen og prøv igjen.")
    linjer.append("Ingenting er bestilt. Åpne Oda, sjekk kurven og trykk «bestill» når du er klar.")
    return "\n".join(linjer)
