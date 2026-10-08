"""Mengder og enheter: tolke "500 g", "3 dl", "2 stk" og regne om til basisenhet.

Dimensjoner: "masse" (gram), "volum" (milliliter), "antall" (stk).
"""
import re

ENHETER = {
    "g": ("masse", 1.0),
    "gram": ("masse", 1.0),
    "kg": ("masse", 1000.0),
    "ml": ("volum", 1.0),
    "cl": ("volum", 10.0),
    "dl": ("volum", 100.0),
    "l": ("volum", 1000.0),
    "liter": ("volum", 1000.0),
    "ss": ("volum", 15.0),
    "ts": ("volum", 5.0),
    "stk": ("antall", 1.0),
    "st": ("antall", 1.0),
}

_TALL = r"(\d+(?:[.,]\d+)?)"
_ENHET = r"(kg|g|gram|ml|cl|dl|l|liter|stk|st)\b"
_PAKKE = re.compile(rf"(?:(\d+)\s*[x×]\s*)?{_TALL}\s*{_ENHET}", re.IGNORECASE)
_MENGDE = re.compile(rf"^\s*{_TALL}\s*([a-zæøå]+)?\s*$", re.IGNORECASE)


def _tall(s: str) -> float:
    return float(s.replace(",", "."))


def basis(mengde: float, enhet: str):
    """(dimensjon, mengde i basisenhet) eller None hvis enheten er ukjent."""
    info = ENHETER.get((enhet or "").strip().lower())
    if not info:
        return None
    dim, faktor = info
    return dim, mengde * faktor


def pakkestorrelse(*tekster: str):
    """Les pakkestørrelse fra produktnavn, f.eks. "TINE Kremfløte 3 dl" -> ("volum", 300).

    Siste treff vinner, fordi Oda legger størrelsen sist i name_extra.
    "4 x 125 g" blir 500 g.
    """
    funnet = None
    for tekst in tekster:
        for m in _PAKKE.finditer(tekst or ""):
            antall = int(m.group(1)) if m.group(1) else 1
            b = basis(_tall(m.group(2)) * antall, m.group(3))
            if b and b[1] > 0:
                funnet = b
    return funnet


def tolk_mengde(tekst: str):
    """Tolk fritekst som "500 g" eller "2 stk" -> (dimensjon, basismengde) eller None."""
    if not tekst:
        return None
    m = _MENGDE.match(str(tekst))
    if not m:
        return pakkestorrelse(str(tekst))
    enhet = m.group(2) or "stk"
    return basis(_tall(m.group(1)), enhet)


def vis(dim: str, mengde: float) -> str:
    """Menneskelig visning av en basismengde."""
    if dim == "masse":
        return f"{mengde / 1000:.1f} kg".replace(".0 kg", " kg") if mengde >= 1000 else f"{mengde:.0f} g"
    if dim == "volum":
        return f"{mengde / 1000:.1f} l".replace(".0 l", " l") if mengde >= 1000 else f"{mengde / 100:.1f} dl".replace(".0 dl", " dl")
    return f"{mengde:.1f} stk".replace(".0 stk", " stk")
