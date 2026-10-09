"""Tynt lag over mcp-oda-CLI-en (forken i vendor/mcp-oda).

Sikkerhet:
- Bare kommandoene i TILLATT kan kjøres. Ingen av dem bestiller eller betaler
  (mcp-oda har ingen slik kommando), og vi stenger i tillegg ute alt som
  fjerner fra kurven, tømmer kurven, velger leveringstid eller legger hele
  oppskrifter i kurven forbi lagersjekken.
- Passordet leses fra en fil med rettighet 600 utenfor repoet og sendes til
  mcp-oda via stdin (--pass-stdin), aldri som argument eller i logg.
"""
import json
import os
import shlex
import shutil
import stat
import subprocess
from pathlib import Path

from . import lagring

TILLATT = {
    ("auth", "user"),
    ("auth", "login"),
    ("product", "search"),
    ("product", "add"),
    ("cart", "list"),
    ("recipe", "search"),
    ("recipe", "details"),
    ("recipe", "ingredients"),
    ("list", "all"),
    ("list", "get"),
    ("list", "create"),
    ("list", "add"),
}

MIDDAG_FILTER = "meal:65"  # Oda sitt "Middag"-filter i oppskriftssøk


class OdaFeil(RuntimeError):
    pass


class IkkeTillatt(OdaFeil):
    pass


def legitimasjonsfil() -> Path:
    return Path(os.environ.get("MIDDAG_CREDENTIALS", Path.home() / ".config" / "middag" / "oda.env"))


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


def _grunnkommando() -> list:
    egen = os.environ.get("MIDDAG_ODA_CMD")
    if egen:
        return shlex.split(egen)
    cli = lagring.ROT / "vendor" / "mcp-oda" / "dist" / "index.js"
    if not cli.exists():
        raise OdaFeil(f"Finner ikke {cli}. Kjør scripts/setup_oda.sh først.")
    if shutil.which("node"):
        return ["node", str(cli)]
    # Ingen Node på maskinen: kjør i container
    return [str(lagring.ROT / "scripts" / "oda-podman.sh")]


def _er_auth_feil(tekst: str) -> bool:
    t = tekst.lower()
    return any(s in t for s in ("authentication may be required", "http 401", "http 403", "not logged in", "login required"))


def _er_innlogget(status: str) -> bool:
    t = status.lower()
    return "logged in as" in t or "successfully logged in" in t


class Oda:
    """Kjører mcp-oda. `kjor` kan byttes ut i tester."""

    def __init__(self, kjor=None):
        self._kjor = kjor or self._subprocess

    @staticmethod
    def _subprocess(args, stdin=None):
        cmd = _grunnkommando()
        datadir = os.environ.get("MIDDAG_ODA_DATA")
        if datadir:
            cmd += ["--data-dir", datadir]
        r = subprocess.run(cmd + args, input=stdin, capture_output=True, text=True, timeout=120)
        return r.returncode, r.stdout, r.stderr

    def _kall(self, args, stdin=None, prov_innlogging=True):
        if tuple(args[:2]) not in TILLATT:
            raise IkkeTillatt(f"Kommandoen '{' '.join(args[:2])}' er ikke tillatt i middagsplanleggeren.")
        kode, ut, feil = self._kjor(args, stdin)
        if kode != 0 and prov_innlogging and args[0] != "auth" and _er_auth_feil(feil + ut):
            self.logg_inn()
            kode, ut, feil = self._kjor(args, stdin)
        if kode != 0:
            raise OdaFeil(f"mcp-oda {' '.join(args[:2])} feilet: {(feil or ut).strip()[:400]}")
        return ut if ut.strip() else feil

    def _json(self, args):
        ut = self._kall(args)
        try:
            return json.loads(ut)
        except json.JSONDecodeError as e:
            raise OdaFeil(f"Uventet svar fra mcp-oda {' '.join(args[:2])}: {ut[:200]}") from e

    # --- innlogging ---

    def logg_inn(self) -> None:
        epost, passord = les_legitimasjon()
        try:
            self._kall(["auth", "login", "--user", epost, "--pass-stdin"], stdin=passord + "\n", prov_innlogging=False)
        except OdaFeil as e:
            raise OdaFeil(f"Innlogging hos Oda feilet. Sjekk e-post/passord i {legitimasjonsfil()}. ({e})") from None

    def bruker(self) -> str:
        return self._kall(["auth", "user"], prov_innlogging=False).strip()

    def sikre_innlogget(self) -> str:
        """Sørg for innlogget sesjon før vi rører kurven.

        Uten innlogging gir Oda en anonym gjestekurv – den skal vi aldri vise
        eller fylle, for det er ikke Oles kurv.
        """
        status = self.bruker()
        if _er_innlogget(status):
            return status
        self.logg_inn()
        status = self.bruker()
        if not _er_innlogget(status):
            raise OdaFeil(f"Fikk ikke bekreftet innlogging hos Oda ({status}).")
        return status

    # --- les ---

    def sok_produkt(self, sok: str, side: int = 1) -> dict:
        return self._json(["product", "search", sok, "--page", str(side)])

    def sok_oppskrift(self, sok: str = None, side: int = 1, filtre=(MIDDAG_FILTER,)) -> dict:
        args = ["recipe", "search"] + ([sok] if sok else []) + ["--page", str(side)]
        if filtre:
            args += ["--filter", *filtre]
        return self._json(args)

    def oppskrift(self, oppskrift_id: int) -> dict:
        return self._json(["recipe", "ingredients", str(int(oppskrift_id))])

    def oppskrift_detaljer(self, oppskrift_id: int) -> dict:
        return self._json(["recipe", "details", str(int(oppskrift_id))])

    def kurv(self) -> dict:
        return self._json(["cart", "list"])

    @staticmethod
    def belop_etter_rabatt(kurv: dict) -> float:
        """Det Oda faktisk belaster for kurven.

        Odas eget `display_price` er bare summen av varelinjenes ordinære pris
        og tar ikke høyde for mengderabatter som "2 for 1" -- den rabatten
        trekkes først fra i `discounted_display_price` (mcp-oda sin
        parseCartApi), som er det som faktisk belastes ved bestilling.
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
        return self._json(["list", "all"])

    # --- skriv (bare legge til) ---

    def legg_i_kurv(self, produkt_id: int, antall: int) -> None:
        if antall < 1:
            return
        self._kall(["product", "add", str(int(produkt_id)), "--count", str(int(antall))])

    def lag_middagsliste(self, tittel: str, beskrivelse: str) -> dict:
        return self._json(["list", "create", tittel, "--description", beskrivelse, "--dinner"])

    def legg_i_liste(self, liste_id: int, produkt_id: int, antall: int) -> None:
        self._kall(["list", "add", str(int(liste_id)), str(int(produkt_id)), "--count", str(int(antall))])
