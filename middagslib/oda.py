"""Oda for middagsplanleggeren: innlogging, sesjon og de få operasjonene vi trenger.

HTTP-delen ligger i oda_klient.py (portert fra mcp-oda). Her ligger det som er
spesifikt for oss: legitimasjonsfilen, ny innlogging når sesjonen er utløpt,
og kontroll av at vi aldri jobber mot Odas anonyme gjestekurv.

Sikkerhet:
- Klienten har ingen metoder for å bestille, betale, fjerne fra kurven, tømme
  den, velge leveringstid eller slette lister, og POST-kall sperres utenfor en
  hviteliste (se oda_klient._post).
- Passordet leses fra en fil med rettighet 600 utenfor repoet og sendes bare i
  innloggingskallet til oda.com, aldri som argument eller i logg.
- Sesjonen (cookies) lagres i ~/.config/middag/oda-cookies.json med rettighet 600.
- Produktkategorien hentes fra den offentlige produktsiden uten cookies.
"""
import html
import json
import os
import re
import stat
import urllib.request
from pathlib import Path

from .oda_klient import AuthFeil, KlientFeil, OdaKlient, Sperret

MIDDAG_FILTER = "meal:65"  # Oda sitt "Middag"-filter i oppskriftssøk


class OdaFeil(RuntimeError):
    pass


class IkkeTillatt(OdaFeil):
    pass


def legitimasjonsfil() -> Path:
    return Path(os.environ.get("MIDDAG_CREDENTIALS", Path.home() / ".config" / "middag" / "oda.env"))


def cookiefil() -> Path:
    return Path(os.environ.get("MIDDAG_ODA_COOKIES", Path.home() / ".config" / "middag" / "oda-cookies.json"))


def les_legitimasjon() -> tuple:
    """Les ODA_EMAIL og ODA_PASSWORD. Nekter hvis filen kan leses av andre."""
    fil = legitimasjonsfil()
    if not fil.exists():
        raise OdaFeil(
            f"Mangler {fil}. Opprett den med:\n"
            f"  mkdir -p {fil.parent} && install -m 600 /dev/null {fil}\n"
            f"  og skriv ODA_EMAIL=... og ODA_PASSWORD=... i den."
        )
    modus = stat.S_IMODE(fil.stat().st_mode)
    if modus & 0o077:
        raise OdaFeil(f"{fil} har rettighet {oct(modus)}. Kjør: chmod 600 {fil}")
    verdier = {}
    for linje in fil.read_text(encoding="utf-8").splitlines():
        linje = linje.strip()
        if not linje or linje.startswith("#") or "=" not in linje:
            continue
        k, v = linje.split("=", 1)
        verdier[k.strip()] = v.strip().strip('"').strip("'")
    epost, passord = verdier.get("ODA_EMAIL"), verdier.get("ODA_PASSWORD")
    if not epost or not passord:
        raise OdaFeil(f"{fil} må inneholde både ODA_EMAIL og ODA_PASSWORD.")
    return epost, passord


class Oda:
    """Odas operasjoner for planleggeren. `klient` kan byttes ut i tester."""

    def __init__(self, klient=None):
        # Gamle installasjoner hadde sesjonen i ~/.mcp-oda; den leses én gang
        self.klient = klient or OdaKlient(cookiefil(), Path.home() / ".mcp-oda" / "cookies.json")

    def _kall(self, operasjon, *args, prov_innlogging=True):
        try:
            return operasjon(*args)
        except AuthFeil:
            if not prov_innlogging:
                raise OdaFeil("Ikke logget inn hos Oda.") from None
            self.logg_inn()
            try:
                return operasjon(*args)
            except KlientFeil as e:
                raise OdaFeil(str(e)) from None
        except Sperret as e:
            raise IkkeTillatt(str(e)) from None
        except KlientFeil as e:
            raise OdaFeil(str(e)) from None

    # --- innlogging ---

    def logg_inn(self) -> None:
        epost, passord = les_legitimasjon()
        try:
            ok = self.klient.logg_inn(epost, passord)
        except KlientFeil as e:
            raise OdaFeil(f"Innlogging hos Oda feilet ({e}).") from None
        if not ok:
            raise OdaFeil(f"Innlogging hos Oda feilet. Sjekk e-post/passord i {legitimasjonsfil()}.")

    def bruker(self) -> str:
        navn = self.klient.bruker()
        return f"Innlogget som {navn}." if navn else "Ikke innlogget."

    def sikre_innlogget(self) -> str:
        """Sørg for innlogget sesjon før vi rører kurven.

        Uten innlogging gir Oda en anonym gjestekurv. Den skal vi aldri vise
        eller fylle, for det er ikke brukerens kurv.
        """
        if self.klient.bruker():
            return self.bruker()
        self.logg_inn()
        if not self.klient.bruker():
            raise OdaFeil("Fikk ikke bekreftet innlogging hos Oda.")
        return self.bruker()

    # --- les ---

    def sok_produkt(self, sok: str, side: int = 1) -> dict:
        return self._kall(self.klient.sok_produkter, sok, side)

    def sok_oppskrift(self, sok: str = None, side: int = 1, filtre=(MIDDAG_FILTER,)) -> dict:
        return self._kall(self.klient.sok_oppskrifter, sok, side, list(filtre or ()))

    def oppskrift(self, oppskrift_id: int) -> dict:
        return self._kall(self.klient.oppskrift_ingredienser, int(oppskrift_id))

    def oppskrift_detaljer(self, oppskrift_id: int) -> dict:
        return self._kall(self.klient.oppskrift_detaljer, int(oppskrift_id))

    def kurv(self) -> dict:
        return self._kall(self.klient.kurv)

    def produkt_kategori(self, produkt_id: int) -> list:
        """Kategoristien til et produkt, f.eks. ["Frukt og grønt", "Frosne grønnsaker"].

        Søkeresultatene har ingen kategori, så vi leser brødsmulestien
        (schema.org BreadcrumbList) fra den offentlige produktsiden.
        """
        url = f"https://oda.com/no/products/{int(produkt_id)}/"
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (middagsplanlegger)"})
            with urllib.request.urlopen(req, timeout=30) as r:
                side = r.read().decode("utf-8", "replace")
        except OSError as e:
            raise OdaFeil(f"Kunne ikke hente produktsiden {url}: {e}") from e
        for blokk in re.findall(r'<script type="application/ld\+json">(.*?)</script>', side, re.S):
            try:
                data = json.loads(blokk)
            except json.JSONDecodeError:
                continue
            if data.get("@type") != "BreadcrumbList":
                continue
            elementer = sorted(data.get("itemListElement", []), key=lambda e: e.get("position", 0))
            # Hjem > Alle produkter > kategorier … > produktet selv
            return [html.unescape(e.get("name", "")) for e in elementer[2:-1]]
        raise OdaFeil(f"Fant ingen kategori på {url}")

    @staticmethod
    def belop_etter_rabatt(kurv: dict) -> float:
        """Det Oda faktisk belaster for kurven.

        Odas eget `display_price` er bare summen av varelinjenes ordinære pris
        og tar ikke høyde for mengderabatter som "2 for 1" -- den rabatten
        trekkes først fra i `discounted_display_price` (oda_klient.kurv), som
        er det som faktisk belastes ved bestilling.
        Faller tilbake til display_price for gamle data/fixtures uten feltet.
        """
        if kurv.get("discounted_display_price") is not None:
            return kurv["discounted_display_price"]
        return kurv.get("display_price") or 0.0

    @staticmethod
    def belop_etter_rabatt_uten(kurv: dict, ider) -> float:
        """Som belop_etter_rabatt, men uten linjene for produktene i `ider`.

        Brukes av minstebeløp-sjekken: varer som planen selv skal legge i kurven
        (f.eks. fra en tidligere runde av samme plan) skal ikke telles to ganger.
        """
        total = Oda.belop_etter_rabatt(kurv)
        for x in kurv.get("items", []):
            if x.get("id") in ider:
                total -= x.get("discounted_line_total", x.get("line_total", 0)) or 0
        return max(0.0, total)

    def lister(self) -> list:
        return self._kall(self.klient.lister)

    # --- skriv (bare legge til) ---

    def legg_i_kurv(self, produkt_id: int, antall: int) -> None:
        if antall < 1:
            return
        self._kall(self.klient.legg_i_kurv, int(produkt_id), int(antall))

    def lag_middagsliste(self, tittel: str, beskrivelse: str) -> dict:
        return self._kall(self.klient.lag_liste, tittel, beskrivelse, True)

    def legg_i_liste(self, liste_id: int, produkt_id: int, antall: int) -> None:
        self._kall(self.klient.legg_i_liste, int(liste_id), int(produkt_id), int(antall))
