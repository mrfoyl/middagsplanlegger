"""Kunnskap om matvarer: holdbarhet, allergener, frysbarhet.

Alt her er nøkkelordbasert og bevisst enkelt. Det er et hjelpemiddel, ikke en
garanti: allergenfilteret fanger vanlige navn, men agenten skal fortsatt se
over ingredienslisten når noen i familien har en allergi.
"""
import re
import unicodedata

# Antall dager varen holder seg etter levering. Første treff vinner, så
# spesifikke ord står før generelle ("kyllingpålegg" før "kylling").
HOLDBARHET = [
    (("fryst", "frossen", "frosne", "frys"), 180),
    # Tørrvarer først, så merkenavn som "Sopps Makaroni" ikke tolkes som sopp
    (("pasta", "makaroni", "spaghetti", "penne", "fusilli", "tagliatelle", "nudler", "couscous", "bulgur", "linser", "jasminris", "basmatiris"), 365),
    (("kokosmelk", "kokosfløte", "havremelk", "havrefløte", "soyamelk", "flatbrød", "knekkebrød", "hermetisk", "boks", "knuste tomater", "hakkede tomater", "polpa", "passata", "tomatpuré", "kokosmelk", "tørket", "tørr", "pulver", "buljong", "kraft", "krydder", "malt"), 365),
    (("pålegg", "bacon", "skinke", "salami", "pølse", "chorizo"), 10),
    (("laks", "torsk", "sei", "ørret", "hyse", "fisk", "reker", "scampi", "skalldyr", "blåskjell", "kveite"), 2),
    (("kjøttdeig", "karbonadedeig", "kyllingdeig", "deig"), 2),
    (("kylling", "kalkun", "andebryst", "andelår"), 3),
    (("biff", "entrecote", "svin", "lam", "storfe", "indrefilet", "kotelett", "koteletter", "strimler", "gryterett"), 4),
    (("persille", "koriander", "basilikum", "gressløk", "dill", "mynte", "timian, fersk", "urter", "ruccola", "salat", "spinat", "isberg", "babyleaf", "grønnkål", "salatmiks", "vasket", "vårløk", "sukkererter"), 4),
    (("bær", "jordbær", "bringebær", "blåbær", "sopp", "champignon", "avokado", "mango"), 4),
    (("tomat", "agurk", "paprika", "squash", "brokkoli", "blomkål", "aspargues", "asparges", "bønner, grønne", "aubergine", "mais, fersk", "sitron", "lime"), 7),
    (("fløte", "rømme", "melk", "yoghurt", "crème fraîche", "creme fraiche", "kesam", "kremost", "cottage", "mozzarella", "burrata"), 7),
    (("ost", "parmesan", "cheddar", "feta", "norvegia", "jarlsberg"), 21),
    (("løk", "hvitløk", "potet", "gulrot", "gulrøtter", "kål", "rotgrønnsak", "kålrot", "pastinakk", "sellerirot", "søtpotet", "eple", "ingefær"), 21),
    (("egg",), 21),
    (("brød", "tortilla", "lompe", "pitabrød", "naan", "rundstykke", "hamburgerbrød", "pølsebrød"), 5),
]
TORRVARE_DAGER = 90
FERSK_GRENSE = 10  # dager; under dette regnes varen som ferskvare

ALLERGENER = {
    "gluten": ("hvete", "hvetemel", "mel", "pasta", "spaghetti", "penne", "fusilli", "tagliatelle", "makaroni", "lasagne", "gnocchi", "couscous", "bulgur", "brød", "tortilla", "lompe", "pita", "naan", "rasp", "panko", "soyasaus", "nudler", "eggnudler", "pizzabunn", "bygg", "rug", "spelt"),
    "melk": ("melk", "fløte", "rømme", "ost", "smør", "yoghurt", "crème fraîche", "creme fraiche", "kesam", "kremost", "cottage", "mozzarella", "parmesan", "cheddar", "feta", "norvegia", "jarlsberg", "burrata", "mascarpone", "ricotta", "halloumi", "matfløte", "kokkefløte"),
    "egg": ("egg", "majones", "aioli", "eggnudler"),
    "nøtter": ("nøtt", "nøtter", "mandel", "mandler", "cashew", "hasselnøtt", "valnøtt", "pistasj", "pekan", "macadamia"),
    "peanøtter": ("peanøtt", "peanøtter", "peanøttsmør", "satay"),
    "fisk": ("fisk", "laks", "torsk", "sei", "makrell", "tunfisk", "ørret", "hyse", "ansjos", "fiskesaus", "kveite", "fiskekaker", "fiskeboller"),
    "skalldyr": ("reker", "scampi", "krabbe", "hummer", "blåskjell", "skjell", "kamskjell", "kreps"),
    "soya": ("soya", "soyasaus", "tofu", "edamame", "miso"),
    "sesam": ("sesam", "tahini"),
    "selleri": ("selleri", "stangselleri", "sellerirot"),
    "sennep": ("sennep",),
}
# Ord som ellers ville gitt falske treff ("kokosmelk" er ikke melk)
ALLERGEN_UNNTAK = {
    "melk": ("kokosmelk", "kokosfløte", "havremelk", "havrefløte", "mandelmelk", "soyamelk", "soyafløte", "smørbrød"),
    "egg": ("lammelegg", "svinelegg", "kyllinglegg", "kalkunlegg", "legg"),
}
# Synonymer brukeren kan skrive
ALLERGEN_ALIAS = {"laktose": "melk", "melkeprotein": "melk", "hvete": "gluten", "nøtt": "nøtter", "peanøtt": "peanøtter", "skjell": "skalldyr"}

FRYSBAR_TAGGER = {"gryter", "supper", "bra restemat"}
FRYSBAR_ORD = ("gryte", "suppe", "lasagne", "chili", "kjøttsaus", "bolognese", "karri", "curry", "kjøttboller", "kjøttkaker", "moussaka", "gulasj", "stuing", "lapskaus", "dal", "enchiladas", "ragu", "pai")
IKKE_FRYSBAR_ORD = ("salat", "wok", "taco", "sushi", "carpaccio", "poke")

# Rettstyper vi ikke vil ha to av samme uke
RETTSTYPER = ("wok", "suppe", "gryte", "pasta", "taco", "salat", "pizza", "lasagne", "burger", "pai", "risotto", "curry", "karri", "lapskaus", "tortilla", "enchiladas", "pannekake", "chili", "omelett", "grateng")

STERKT_ORD = ("chili", "jalapeño", "jalapeno", "sriracha", "sterk", "habanero", "cayenne", "harissa", "sambal")


def norm(tekst: str) -> str:
    """Små bokstaver, fjern tegnsetting, behold æøå."""
    t = unicodedata.normalize("NFC", (tekst or "").lower())
    t = re.sub(r"[^\wæøå ]+", " ", t)
    return re.sub(r"\s+", " ", t).strip()


def inneholder(tekst: str, ord_: str) -> bool:
    """Ord-treff som tåler norske sammensetninger.

    Korte ord (≤3 tegn, som "ost" og "egg") må stå som eget ord eller sist i et
    sammensatt ord ("geitost"), så "ost" ikke treffer "biffstrimler" og
    "egg" ikke treffer "legg". Lengre ord kan stå hvor som helst ("hvetemel").
    """
    t = norm(tekst)
    o = norm(ord_)
    if not o:
        return False
    if len(o) <= 3:
        return re.search(rf"(?:^|\s|\w{{3}}){re.escape(o)}(?:$|\s)", t) is not None
    return o in t


def holdbarhet(*tekster: str, basis: bool = False) -> int:
    tekst = " ".join(t for t in tekster if t)
    for ordliste, dager in HOLDBARHET:
        if any(inneholder(tekst, o) for o in ordliste):
            return dager
    return 365 if basis else TORRVARE_DAGER


def er_fersk(*tekster: str, basis: bool = False) -> bool:
    return holdbarhet(*tekster, basis=basis) <= FERSK_GRENSE


def allergen_nokkel(navn: str) -> str:
    n = norm(navn)
    return ALLERGEN_ALIAS.get(n, n)


def allergen_treff(tekst: str, allergier, unngaa=()) -> list:
    """Hvilke allergier/unngå-ord treffer teksten."""
    treff = []
    for a in allergier:
        nokkel = allergen_nokkel(a)
        ord_ = ALLERGENER.get(nokkel, (nokkel,))
        renset = norm(tekst)
        for unntak in ALLERGEN_UNNTAK.get(nokkel, ()):
            renset = renset.replace(norm(unntak), " ")
        if any(inneholder(renset, o) for o in ord_):
            treff.append(a)
    for u in unngaa:
        if inneholder(tekst, u):
            treff.append(u)
    return treff


def er_frysbar(navn: str, tagger=()) -> bool:
    n = norm(navn)
    if any(inneholder(n, o) for o in IKKE_FRYSBAR_ORD):
        return False
    if any(norm(t) in FRYSBAR_TAGGER for t in tagger):
        return True
    return any(inneholder(n, o) for o in FRYSBAR_ORD)


def er_sterk(tekst: str) -> bool:
    return any(inneholder(tekst, o) for o in STERKT_ORD)


def minutter(varighet: str):
    """'1 t 10 min' -> 70, '20 min' -> 20, '' -> None."""
    if not varighet:
        return None
    t = re.search(r"(\d+)\s*t", varighet)
    m = re.search(r"(\d+)\s*min", varighet)
    if not t and not m:
        return None
    return (int(t.group(1)) * 60 if t else 0) + (int(m.group(1)) if m else 0)


def rettstyper(navn: str) -> set:
    return {t for t in RETTSTYPER if inneholder(navn, t)}


# --- sparemodus: billige middager som holder i to uker ---

SPAR_MIN_DAGER = 14
# Råvarer som finnes frosne hos Oda, eller som tåler å fryses ned ved levering
FRYSEERSTATTBAR = (
    "kylling", "kalkun", "kjøttdeig", "karbonadedeig", "deig", "laks", "torsk", "sei", "hyse", "fisk", "reker",
    "scampi", "brokkoli", "blomkål", "spinat", "erter", "bønner", "mais", "wok", "grønnsak", "bær", "kjøttboller",
    "kjøttkaker", "karbonader", "svin", "biff", "strimler", "pølse", "bacon", "skinke", "kjøtt",
    "paprika", "grytebase", "suppebase", "brød", "rundstykke", "lompe", "pita", "naan",
)

# Uåpnet holdbarhet der den er vesentlig lengre enn det hovedtabellen bruker
# (som regner med at åpnede rester må brukes raskt). Brukes bare til å vurdere
# om varene holder i to uker i sparemodus.
UAPNET = [
    (("revet",), 30),
    (("tortilla",), 30),
    (("fløte", "rømme", "crème fraîche", "creme fraiche", "kesam", "kremost", "cottage", "matfløte"), 21),
    (("sitron", "lime"), 21),
    (("melk", "yoghurt"), 10),
]


def holdbarhet_uapnet(*tekster: str, basis: bool = False) -> int:
    """Kan bare forlenge hovedtabellens anslag ("kokosmelk" forblir langholdbar)."""
    tekst = " ".join(t for t in tekster if t)
    vanlig = holdbarhet(tekst, basis=basis)
    for ordliste, dager in UAPNET:
        if any(inneholder(tekst, o) for o in ordliste):
            return max(vanlig, dager)
    return vanlig


def korte_ingredienser(r: dict) -> list:
    """Ikke-basis-ingredienser som holder kortere enn to uker."""
    return [i for i in r["ingredienser"] if not i.get("is_basic")
            and holdbarhet_uapnet(i["title"], (i.get("product") or {}).get("full_name", "")) < SPAR_MIN_DAGER]


def fryseerstattbar(tittel: str) -> bool:
    return any(inneholder(tittel, o) for o in FRYSEERSTATTBAR)


def ikke_erstattbare(r: dict) -> list:
    """Kortholdbare ingredienser som verken finnes frosne eller kan fryses (urter, salat …)."""
    return [i for i in korte_ingredienser(r) if not fryseerstattbar(i["title"])]


def spar_egnet(r: dict, maks_ikke_erstattbare: int = 1) -> bool:
    return len(ikke_erstattbare(r)) <= maks_ikke_erstattbare
