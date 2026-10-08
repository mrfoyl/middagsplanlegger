"""Hent aktivitetsdager fra Google-kalenderen via gcalendar.py i openclaw-workspace.

Valgfritt: slås på med `profil sett kalender ja` og søkeord som
`profil sett kalender_sokeord "fotball,svømming,turn"`. Feiler stille med en
melding hvis kalenderen ikke er tilgjengelig – da brukes bare faste dager.
"""
import datetime as dt
import os
import sys
from pathlib import Path

from . import matvarer, profil as profilmod
from .planlegger import datoer


def _gcalendar_katalog() -> Path:
    return Path(os.environ.get("MIDDAG_GCALENDAR_DIR", Path.home() / ".openclaw" / "workspace"))


def aktivitetsdager(uke: str, sokeord) -> tuple:
    """(set med ukedager, melding)."""
    if not sokeord:
        return set(), "Kalender er på, men ingen søkeord er satt (profil sett kalender_sokeord ...)."
    katalog = _gcalendar_katalog()
    if not (katalog / "gcalendar.py").exists():
        return set(), f"Fant ikke gcalendar.py i {katalog}; bruker bare faste aktivitetsdager."
    try:
        sys.path.insert(0, str(katalog))
        import gcalendar
        from googleapiclient.discovery import build

        mandag = dt.date.fromisoformat(datoer(uke)["man"])
        tz = dt.datetime.now().astimezone().tzinfo
        start = dt.datetime.combine(mandag, dt.time(0), tz)
        slutt = start + dt.timedelta(days=7)
        tjeneste = build("calendar", "v3", credentials=gcalendar.get_creds())
        hendelser = tjeneste.events().list(
            calendarId="primary", timeMin=start.isoformat(), timeMax=slutt.isoformat(),
            singleEvents=True, orderBy="startTime", maxResults=250,
        ).execute().get("items", [])
    except Exception as e:  # kalender er et tillegg, aldri et stopp
        return set(), f"Kunne ikke lese kalenderen ({e.__class__.__name__}: {e}); bruker bare faste aktivitetsdager."
    finally:
        if sys.path and sys.path[0] == str(katalog):
            sys.path.pop(0)

    dager, funnet = set(), []
    for h in hendelser:
        tekst = f"{h.get('summary', '')} {h.get('description', '')}"
        if not any(matvarer.inneholder(tekst, o) for o in sokeord):
            continue
        s = h.get("start", {})
        tid = s.get("dateTime") or s.get("date")
        dato = dt.date.fromisoformat(tid[:10])
        dag = profilmod.UKEDAGER[dato.weekday()]
        dager.add(dag)
        funnet.append(f"{dag}: {h.get('summary', '')}")
    return dager, ("Fra kalenderen: " + "; ".join(funnet)) if funnet else "Ingen aktiviteter funnet i kalenderen."
