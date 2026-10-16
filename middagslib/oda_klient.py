"""HTTP-klient mot Oda.com, portert fra mcp-oda (TypeScript) til Python.

Bygger på oda-client.ts i https://github.com/agfagerbakk/mcp-oda
(commit 717fb4e, MIT-lisens, se LICENSE-mcp-oda). Vi har bare tatt med det
middagsplanleggeren bruker: innlogging, produktsøk, legge i kurven, lese
kurven, oppskrifter og egne middagslister. Svarene har samme format som
mcp-oda-CLI-en, så resten av koden er uendret.

Sikkerhet: Klienten har ingen metoder for å fjerne fra kurven, tømme den,
velge leveringstid, slette lister eller bestille. I tillegg går alle POST-kall
gjennom `_post`, som bare slipper gjennom endepunktene i POST_TILLATT og
nekter negative mengder og `delete`. Cookies sendes bare til oda.com.
"""
import json
import re
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

BASE_URL = "https://oda.com/no"
API_BASE = "https://oda.com"
CART_API = "https://oda.com/api/v1/cart/"
CART_ITEMS_API = "https://oda.com/api/v1/cart/items/"
LOGIN_API = "https://oda.com/api/v1/user/login/"
PRODUCT_LISTS_API = "https://oda.com/api/v1/product-lists/"
SLOT_PICKER_API = "https://oda.com/api/v1/slot-picker/slots/"  # bare lesing, for brukernavn

# Alle POST-endepunkter klienten får bruke. Liste-ID-er er tall.
POST_TILLATT = (
    re.compile(re.escape(CART_ITEMS_API) + r"$"),
    re.compile(re.escape(LOGIN_API) + r"$"),
    re.compile(re.escape(PRODUCT_LISTS_API) + r"$"),
    re.compile(re.escape(PRODUCT_LISTS_API) + r"\d+/products/$"),
)

HODER = {
    "User-Agent": "Mozilla/5.0 (X11; Linux aarch64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Accept-Language": "no,nb;q=0.9,en;q=0.8",
}
HTML_ACCEPT = "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"
TIDSAVBRUDD = 60


class KlientFeil(RuntimeError):
    pass


class AuthFeil(KlientFeil):
    """HTTP 401/403: sesjonen mangler eller er utløpt."""


class Sperret(KlientFeil):
    """Et kall klienten aldri skal gjøre."""


# --- transport ---

class _IngenOmdirigering(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


_OPENER = urllib.request.build_opener(_IngenOmdirigering)


def http(metode: str, url: str, hoder: dict, data: bytes = None) -> tuple:
    """Ett HTTP-kall uten omdirigering. (status, [(navn, verdi)], kropp)."""
    req = urllib.request.Request(url, data=data, headers=hoder, method=metode)
    try:
        with _OPENER.open(req, timeout=TIDSAVBRUDD) as r:
            return r.status, list(r.headers.items()), r.read()
    except urllib.error.HTTPError as e:
        return e.code, list(e.headers.items()) if e.headers else [], e.read() or b""
    except OSError as e:
        raise KlientFeil(f"Nettverksfeil mot {urllib.parse.urlsplit(url).netloc}: {e}") from e


class Svar:
    def __init__(self, status, hoder, kropp, url):
        self.status, self.hoder, self.kropp, self.url = status, hoder, kropp, url

    @property
    def ok(self) -> bool:
        return 200 <= self.status < 300

    def tekst(self) -> str:
        return self.kropp.decode("utf-8", "replace")

    def json(self):
        try:
            return json.loads(self.kropp)
        except ValueError as e:
            raise KlientFeil(f"Ugyldig JSON fra {self.url}: {self.tekst()[:120]}") from e


def _paa_oda(url: str) -> bool:
    d = urllib.parse.urlsplit(url)
    return d.scheme == "https" and d.hostname == "oda.com"


# --- HTML-parsing (Next.js) ---

def match_balansert(s: str, start: int):
    """Fra en åpningsparentes ( [ { ved `start`: delstrengen til og med den som lukker den.

    Hopper over JSON-strenger og escapes, så parenteser inni tekst ikke teller.
    """
    apne = s[start]
    lukk = {"(": ")", "[": "]", "{": "}"}.get(apne)
    if not lukk:
        return None
    dybde, i_streng, i = 0, False, start
    n = len(s)
    while i < n:
        c = s[i]
        if i_streng:
            if c == "\\":
                i += 2
                continue
            if c == '"':
                i_streng = False
        elif c == '"':
            i_streng = True
        elif c == apne:
            dybde += 1
        elif c == lukk:
            dybde -= 1
            if dybde == 0:
                return s[start:i + 1]
        i += 1
    return None


def _flight(html: str) -> str:
    """Slå sammen strengene i alle `self.__next_f.push([...])` til én flight-strøm."""
    merke = "self.__next_f.push("
    deler = []
    i = 0
    while (i := html.find(merke, i)) != -1:
        start = i + len(merke) - 1
        arg = match_balansert(html, start)
        if not arg:
            i = start + 1
            continue
        i = start + len(arg)
        try:
            arr = json.loads(arg[1:-1])
        except ValueError:
            continue
        if isinstance(arr, list):
            deler += [x for x in arr if isinstance(x, str)]
    return "".join(deler)


def _sporringer(flight: str) -> list:
    """Alle `"queries":[…]` fra React Query-cachen i flight-strømmen."""
    nal = '"queries":'
    ut = []
    i = 0
    while (i := flight.find(nal, i)) != -1:
        i += len(nal)
        start = flight.find("[", i)
        if start == -1 or flight[i:start].strip():
            continue
        tekst = match_balansert(flight, start)
        if not tekst:
            continue
        i = start + len(tekst)
        try:
            data = json.loads(tekst)
        except ValueError:
            continue
        if isinstance(data, list):
            ut += data
    return ut


def next_data(html: str):
    """__NEXT_DATA__, eller den samme formen bygd fra App Router-strømmen."""
    m = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', html, re.S)
    if m:
        try:
            return json.loads(m.group(1))
        except ValueError:
            pass
    flight = _flight(html)
    if not flight:
        return None
    sporringer = _sporringer(flight)
    if not sporringer:
        return None
    return {"props": {"pageProps": {"dehydratedState": {"queries": sporringer}}}}


def finn_sporring(data, nokkel: str):
    """state.data for spørringen med queryKey[0] == nokkel (eller {_id: nokkel})."""
    sporringer = (((data or {}).get("props") or {}).get("pageProps") or {}).get("dehydratedState", {}).get("queries") or []
    for q in sporringer:
        k = q.get("queryKey")
        if not isinstance(k, list) or not k:
            continue
        forste = k[0]
        if forste == nokkel or (isinstance(forste, dict) and forste.get("_id") == nokkel):
            return (q.get("state") or {}).get("data")
    return None


def _tall(x) -> float:
    try:
        return float(x)
    except (TypeError, ValueError):
        return 0.0


def _tilgjengelighet(raa) -> dict:
    """Søket bruker camelCase, kurv-API-et snake_case. Mangler data, regnes varen som tilgjengelig."""
    a = raa or {}
    er = a.get("isAvailable", a.get("is_available"))
    return {
        "is_available": er is not False,
        "code": a.get("code") or ("unavailable" if er is False else "available"),
        "description": (a.get("descriptionShort") or a.get("description_short") or a.get("description") or "").strip(),
    }


# --- klienten ---

class OdaKlient:
    def __init__(self, cookie_fil: Path, gammel_cookie_fil: Path = None, transport=None):
        self.cookie_fil = Path(cookie_fil)
        self._http = transport or http
        self.cookies = {}
        if self.cookie_fil.exists():
            self._last_cookies(self.cookie_fil)
        elif gammel_cookie_fil and Path(gammel_cookie_fil).exists():
            # Flytt sesjonen fra en gammel mcp-oda-installasjon hit, én gang
            self._last_cookies(Path(gammel_cookie_fil))
            if self.cookies:
                try:
                    self.lagre_cookies()
                except OSError:
                    pass

    # cookies

    def _last_cookies(self, fil: Path) -> None:
        try:
            raa = json.loads(fil.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        if isinstance(raa, list):  # Playwright-format
            self.cookies = {c["name"]: str(c["value"]) for c in raa if c.get("name") and "value" in c}
        elif isinstance(raa, dict):
            self.cookies = {k: str(v) for k, v in raa.items()}

    def lagre_cookies(self) -> None:
        self.cookie_fil.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        tmp = self.cookie_fil.with_suffix(".tmp")
        tmp.touch(mode=0o600)
        tmp.chmod(0o600)
        tmp.write_text(json.dumps(self.cookies, indent=2), encoding="utf-8")
        tmp.replace(self.cookie_fil)

    def _oppdater_cookies(self, hoder) -> None:
        endret = False
        for navn, verdi in hoder:
            if navn.lower() != "set-cookie":
                continue
            del_ = verdi.split(";")[0]
            if "=" not in del_:
                continue
            k, v = del_.split("=", 1)
            k, v = k.strip(), v.strip()
            if k and self.cookies.get(k) != v:
                self.cookies[k] = v
                endret = True
        # Lagre fornyede sesjoner, men aldri opprett filen av et lesekall
        if endret and self.cookie_fil.exists():
            try:
                self.lagre_cookies()
            except OSError:
                pass

    def _cookie_hode(self) -> str:
        return "; ".join(f"{k}={v}" for k, v in self.cookies.items())

    # HTTP

    def _kall(self, metode: str, url: str, accept: str, ekstra=None, data=None) -> Svar:
        if not _paa_oda(url):
            raise Sperret(f"Nekter å sende cookies til {url}")
        hoder = dict(HODER, Accept=accept, Cookie=self._cookie_hode())
        hoder.update(ekstra or {})
        status, svar_hoder, kropp = self._http(metode, url, hoder, data)
        self._oppdater_cookies(svar_hoder)
        return Svar(status, svar_hoder, kropp, url)

    def _get(self, url: str, accept: str = HTML_ACCEPT, ekstra=None) -> Svar:
        """GET som følger omdirigeringer selv, så cookies fra hvert steg tas vare på."""
        for _ in range(10):
            svar = self._kall("GET", url, accept, ekstra)
            if svar.status not in (301, 302, 303, 307, 308):
                return svar
            neste = next((v for k, v in svar.hoder if k.lower() == "location"), None)
            if not neste:
                return svar
            url = urllib.parse.urljoin(url, neste)
        raise KlientFeil(f"For mange omdirigeringer fra {url}")

    def _api_hoder(self, referer: str = None) -> dict:
        h = {"Origin": API_BASE, "Referer": referer or f"{BASE_URL}/"}
        if self.cookies.get("csrftoken"):
            h["X-CSRFToken"] = self.cookies["csrftoken"]
        return h

    def _api_get(self, url: str) -> Svar:
        return self._get(url, "application/json", self._api_hoder())

    def _post(self, url: str, kropp, referer: str = None) -> Svar:
        if not any(m.match(url) for m in POST_TILLATT):
            raise Sperret(f"POST til {url} er ikke tillatt")
        varer = kropp.get("items", []) if isinstance(kropp, dict) else kropp if isinstance(kropp, list) else []
        for v in varer:
            if not isinstance(v, dict) or v.get("delete") or "product_list_id" in v or "recipe_id" in v or _tall(v.get("quantity")) <= 0:
                raise Sperret(f"Bare positive mengder av enkeltprodukter kan legges til: {v}")
        ekstra = dict(self._api_hoder(referer), **{"Content-Type": "application/json"})
        return self._kall("POST", url, "application/json", ekstra, json.dumps(kropp).encode())

    @staticmethod
    def _feil(operasjon: str, svar: Svar):
        hint = " (authentication may be required or expired)" if svar.status in (401, 403) else ""
        tekst = svar.tekst()[:500]
        melding = f"{operasjon} failed: HTTP {svar.status}{hint}" + (f" – {tekst}" if tekst else "")
        return AuthFeil(melding) if svar.status in (401, 403) else KlientFeil(melding)

    def _side(self, url: str):
        svar = self._get(url)
        if svar.status == 425:
            raise KlientFeil("Server returned 425 Too Early. Please try again later.")
        return next_data(svar.tekst())

    # produkter

    def sok_produkter(self, sok: str, side: int = 1) -> dict:
        url = f"{BASE_URL}/search/products/?q={urllib.parse.quote(sok)}" + (f"&page={side}" if side > 1 else "")
        nd = self._side(url)
        data = finn_sporring(nd, "mixedSearch") or finn_sporring(nd, "searchpageresponse")
        if not data or not data.get("items"):
            return {"page_url": url, "items": [], "has_more": False}
        varer = []
        for x in data["items"]:
            if x.get("type") != "product" or not x.get("attributes"):
                continue
            a = x["attributes"]
            enhet = a.get("unitPriceQuantityAbbreviation") or ""
            varer.append({
                "id": a.get("id") or x.get("id"),
                "name": a.get("fullName") or a.get("name") or "Unknown",
                "subtitle": a.get("nameExtra") or "",
                "price": _tall(a.get("grossPrice")),
                "relative_price": _tall(a.get("grossUnitPrice")),
                "relative_price_unit": f"/{enhet}" if enhet else "",
                "availability": _tilgjengelighet(a.get("availability")),
            })
        return {"page_url": url, "items": varer, "has_more": (data.get("attributes") or {}).get("hasMoreItems") is True}

    # kurv

    @staticmethod
    def _vare(item: dict) -> dict:
        p = item.get("product") or {}
        enhet = p.get("unit_price_quantity_abbreviation") or ""
        return {
            "id": p.get("id"),
            "name": p.get("full_name") or p.get("name") or "Unknown Product",
            "subtitle": p.get("name_extra") or "",
            "quantity": item.get("quantity") or 1,
            "price": _tall(p.get("gross_price")),
            "relative_price": _tall(p.get("gross_unit_price")),
            "relative_price_unit": f"/{enhet}" if enhet else "",
            "availability": _tilgjengelighet(p.get("availability")),
        }

    def kurv(self) -> dict:
        svar = self._api_get(CART_API)
        if svar.status == 425:
            raise KlientFeil("Server returned 425 Too Early. Please try again later.")
        if not svar.ok:  # aldri late som en auth-feil er en tom kurv
            raise self._feil("Get cart contents", svar)
        data = svar.json()
        raa = [(x, None) for x in data.get("items") or []]
        for g in data.get("groups") or []:
            raa += [(x, g) for x in g.get("items") or []]
        linjer = []
        for item, gruppe in raa:
            v = self._vare(item)
            linjesum = _tall(item.get("display_price_total")) or v["price"] * v["quantity"]
            # Mengderabatter ("2 for 1") står bare i discounted_display_price_total
            rabattert = _tall(item["discounted_display_price_total"]) if item.get("discounted_display_price_total") is not None else linjesum
            linje = v | {"item_id": item.get("item_id") or 0, "line_total": linjesum, "discounted_line_total": rabattert}
            if gruppe and gruppe.get("title"):
                linje["group_title"] = gruppe["title"]
            if gruppe and gruppe.get("group_type"):
                linje["group_type"] = gruppe["group_type"]
            linjer.append(linje)
        return {
            "label_text": data.get("label_text") or "",
            "product_quantity_count": data.get("product_quantity_count") or 0,
            "display_price": _tall(data.get("display_price")),
            "total_gross_amount": _tall(data.get("total_gross_amount")),
            "discounted_display_price": round(sum(x["discounted_line_total"] for x in linjer), 2),
            "items": linjer,
        }

    def legg_i_kurv(self, produkt_id: int, antall: int = 1) -> None:
        svar = self._post(CART_ITEMS_API, {"items": [{"product_id": int(produkt_id), "quantity": int(antall)}]})
        if not svar.ok:
            raise self._feil("Add to cart", svar)

    # oppskrifter

    def sok_oppskrifter(self, sok: str = None, side: int = 1, filtre=()) -> dict:
        param = {}
        if sok:
            param["q"] = sok
        if side > 1:
            param["page"] = str(side)
        if filtre:
            param["filters"] = ",".join(filtre)
        qs = urllib.parse.urlencode(param)
        url = f"{BASE_URL}/recipes/all/" + (f"?{qs}" if qs else "")
        nd = self._side(url)
        data = finn_sporring(nd, "mixedSearch") or finn_sporring(nd, "searchresponse")
        if not data or not data.get("items"):
            return {"page_url": url, "filters": [], "items": [], "has_more": False}
        filtre_ut = []
        for f in data.get("filters") or []:
            if f.get("type") == "filtergroup" and f.get("items"):
                kategori = f.get("displayName") or f.get("name") or "Unknown"
                for o in f["items"]:
                    filtre_ut.append({"id": f"{o.get('name')}:{o.get('value')}", "name": o.get("displayValue") or o.get("value") or "",
                                      "count": o.get("count") or 0, "category": kategori})
            elif f.get("type") == "filter":
                filtre_ut.append({"id": f"{f.get('name')}:{f.get('value')}", "name": f.get("displayValue") or f.get("value") or "",
                                  "count": f.get("count") or 0, "category": "Filter"})
        varer = []
        for x in data["items"]:
            if x.get("type") != "recipe" or not x.get("attributes"):
                continue
            a = x["attributes"]
            r = {"id": a.get("id") or x.get("id"), "name": a.get("title") or "Unknown Recipe"}
            for k, v in (("image_url", a.get("featureImageUrl")), ("duration", a.get("cookingDurationString")),
                         ("difficulty", a.get("difficultyString") or a.get("difficulty"))):
                if v:
                    r[k] = v
            varer.append(r)
        return {"page_url": url, "filters": filtre_ut, "items": varer, "has_more": (data.get("attributes") or {}).get("hasMoreItems") is True}

    def _oppskriftdata(self, oppskrift_id: int) -> dict:
        nd = self._side(f"{BASE_URL}/recipes/{int(oppskrift_id)}")
        if not nd:
            raise KlientFeil(f"Could not load recipe page for ID {oppskrift_id}")
        data = finn_sporring(nd, "recipeDetailApi") or finn_sporring(nd, "get-recipe-detail")
        if not data:
            raise KlientFeil(f"Could not find recipe data for ID {oppskrift_id}")
        return data

    def oppskrift_detaljer(self, oppskrift_id: int) -> dict:
        d = self._oppskriftdata(oppskrift_id)

        def linje(i):
            n = _tall(i.get("displayQuantity"))
            mengde = str(int(n)) if n == int(n) else str(n)
            return f"{mengde} {i.get('displayUnit') or ''} {i.get('title') or ''}".strip()

        ut = {
            "name": d.get("title") or "Unknown",
            "description": d.get("lead") or "",
            "ingredients": [linje(i) for i in d.get("ingredientsDisplayList") or []],
            "instructions": [s.get("text") or "" for s in (d.get("instructions") or {}).get("instructions") or []],
        }
        if d.get("featureImageUrl"):
            ut["image_url"] = d["featureImageUrl"]
        return ut

    def oppskrift_ingredienser(self, oppskrift_id: int) -> dict:
        d = self._oppskriftdata(oppskrift_id)
        visning = {x.get("id"): x for x in d.get("ingredientsDisplayList") or []}
        ingredienser = []
        for ing in d.get("ingredients") or []:
            v = visning.get(ing.get("id")) or {}
            p = ing.get("product") or {}
            ingrediens = ing.get("ingredient") or {}
            ingredienser.append({
                "ingredient_id": ingrediens.get("id"),
                "title": ingrediens.get("title") or v.get("title") or "",
                "is_basic": ing.get("isBasic") is True,
                "packages_per_portion": _tall(ing.get("portionQuantity")),
                "display_quantity": _tall(v.get("displayQuantity")),
                "display_unit": v.get("displayUnit") or (ing.get("portionUnit") or {}).get("abbreviation") or "",
                "group": ing.get("groupHeading") or "",
                "product": {
                    "id": p["id"],
                    "name": p.get("name") or "",
                    "full_name": p.get("fullName") or "",
                    "name_extra": p.get("nameExtra") or "",
                    "price": _tall(p.get("grossPrice")),
                    "available": (p.get("availability") or {}).get("isAvailable") is not False,
                } if p.get("id") else None,
            })
        return {
            "id": d.get("id") or int(oppskrift_id),
            "name": d.get("title") or "Unknown",
            "default_portions": d.get("defaultNumPortions") or 4,
            "duration": d.get("cookingDurationString") or d.get("totalDurationString") or "",
            "difficulty": d.get("difficultyString") or "",
            "tags": [{"id": t.get("id"), "title": t.get("title") or "", "group": t.get("groupKey")} for t in d.get("tags") or []],
            "url": d.get("frontUrl") or f"{BASE_URL}/recipes/{int(oppskrift_id)}/",
            "ingredients": ingredienser,
        }

    # lister (egne middager)

    @staticmethod
    def _liste(d: dict) -> dict:
        return {
            "id": d.get("id"),
            "title": d.get("title"),
            "description": d.get("description") or "",
            "number_of_products": d.get("number_of_products") or 0,
            "total_quantity": d.get("total_quantity") or 0,
            "is_dinner_list": bool(d.get("is_dinner_list")),
        }

    def lister(self) -> list:
        ut, sett, url = [], set(), PRODUCT_LISTS_API
        while url and url not in sett:  # DRF-paginering, med vern mot løkker og andre domener
            sett.add(url)
            svar = self._api_get(url)
            if not svar.ok:
                raise self._feil("Get product lists", svar)
            data = svar.json()
            ut += [self._liste(r) for r in data.get("results") or []]
            neste = data.get("next")
            url = urllib.parse.urljoin(API_BASE + "/", str(neste)) if neste else None
            if url and not _paa_oda(url):
                raise Sperret(f"Nekter pagineringsadresse utenfor oda.com: {neste}")
        return ut

    def lag_liste(self, tittel: str, beskrivelse: str = "", middag: bool = False) -> dict:
        svar = self._post(PRODUCT_LISTS_API, {"title": tittel, "description": beskrivelse, "is_dinner_list": middag})
        if not svar.ok:
            raise self._feil("Create product list", svar)
        return self._liste(svar.json())

    def legg_i_liste(self, liste_id: int, produkt_id: int, antall: int = 1) -> dict:
        svar = self._post(f"{PRODUCT_LISTS_API}{int(liste_id)}/products/", [{"product_id": int(produkt_id), "quantity": int(antall)}])
        if not svar.ok:
            raise self._feil("Add products to list", svar)
        data = svar.json()
        return self._liste(data) | {"items": [self._vare(x) for x in data.get("items") or []]}

    # innlogging

    def logg_inn(self, epost: str, passord: str) -> bool:
        self._get(f"{BASE_URL}/user/login/")  # gir csrftoken
        svar = self._post(LOGIN_API, {"username": epost, "password": passord}, f"{BASE_URL}/user/login/")
        if svar.ok:
            self.lagre_cookies()
            return True
        if svar.status in (400, 401, 403):
            return False
        raise self._feil("Login", svar)

    def bruker(self):
        """Navnet på innlogget bruker, eller None hvis sesjonen ikke er gyldig."""
        try:
            navn = self._navn_fra_side(self._side(f"{BASE_URL}/cart/"))
            if navn:
                return navn
        except KlientFeil:
            pass
        # Nyere sider gir ikke brukeren i klartekst: et autentisert API-kall avgjør
        try:
            if not self._api_get(PRODUCT_LISTS_API).ok:
                return None
        except KlientFeil:
            return None
        try:
            svar = self._api_get(f"{SLOT_PICKER_API}?num-days=1&from-index=0")
            if svar.ok:
                adr = (svar.json().get("delivery_addresses") or [{}])[0]
                if isinstance(adr.get("recipient_name"), str) and adr["recipient_name"]:
                    return adr["recipient_name"]
        except (KlientFeil, IndexError, AttributeError):
            pass
        return "Authenticated Oda user"

    @staticmethod
    def _navn_fra_side(nd):
        b = finn_sporring(nd, "user") if nd else None
        if isinstance(b, dict):
            navn = f"{b.get('firstName') or ''} {b.get('lastName') or ''}".strip()
            return navn or b.get("email") or None
        return None
