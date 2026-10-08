"""Tester mot ekte Oda-oppskriftsdata (tests/fixtures), uten nettverk."""
import json
import os
import stat
import sys
import tempfile
import unittest
from pathlib import Path

ROT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROT))

from middagslib import enheter, handleliste, lager, matvarer, oppskrifter, planlegger, profil  # noqa: E402
from middagslib.oda import IkkeTillatt, Oda, OdaFeil  # noqa: E402

FIX = ROT / "tests" / "fixtures"
UKE = "2026-W42"


def fixture_oppskrift(oid):
    return json.loads((FIX / f"recipe_{oid}.json").read_text())


class FalskOda:
    """Svarer som mcp-oda, men fra fixtures. Registrerer alt som legges i kurven."""

    def __init__(self):
        self.innlogget = True
        self.feil_for = set()
        self.kurv_innhold = {}
        self.lagt_til = []
        self.lister_laget = []
        self.liste_varer = []

    def sok_oppskrift(self, sok=None, side=1, filtre=("meal:65",)):
        fil = FIX / f"search_{sok}.json"
        if fil.exists():
            return json.loads(fil.read_text())
        return {"items": []}

    def oppskrift(self, oid):
        return fixture_oppskrift(oid)

    def kurv(self):
        items = [{"id": k, "quantity": v} for k, v in self.kurv_innhold.items()]
        return {"items": items, "product_quantity_count": sum(self.kurv_innhold.values()), "total_gross_amount": 0}

    def sikre_innlogget(self):
        if not self.innlogget:
            raise OdaFeil("Mangler legitimasjon")
        return "Logged in as: Ole"

    def legg_i_kurv(self, pid, antall):
        if pid in self.feil_for:
            raise OdaFeil("HTTP 500")
        self.lagt_til.append((pid, antall))
        self.kurv_innhold[pid] = self.kurv_innhold.get(pid, 0) + antall

    def lag_middagsliste(self, tittel, beskrivelse):
        self.lister_laget.append((tittel, beskrivelse))
        return {"id": 999, "title": tittel}

    def legg_i_liste(self, liste_id, pid, antall):
        self.liste_varer.append((liste_id, pid, antall))


class MedData(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        os.environ["MIDDAG_DATA"] = self._tmp.name
        self.oda = FalskOda()
        self.p = profil.last()

    def tearDown(self):
        self._tmp.cleanup()
        os.environ.pop("MIDDAG_DATA", None)

    def r(self, oid):
        return oppskrifter.fra_oda(fixture_oppskrift(oid))


class TestEnheter(unittest.TestCase):
    def test_pakkestorrelse(self):
        self.assertEqual(enheter.pakkestorrelse("TINE Kremfløte 3 dl"), ("volum", 300.0))
        self.assertEqual(enheter.pakkestorrelse("Maks 10 per kunde, Norge, 2 stk"), ("antall", 2.0))
        self.assertEqual(enheter.pakkestorrelse("Yoghurt 4 x 125 g"), ("masse", 500.0))
        self.assertEqual(enheter.pakkestorrelse("Rapsolje 1 l"), ("volum", 1000.0))
        self.assertIsNone(enheter.pakkestorrelse("Hvitløk 2-3pk"))

    def test_tolk_mengde(self):
        self.assertEqual(enheter.tolk_mengde("500 g"), ("masse", 500.0))
        self.assertEqual(enheter.tolk_mengde("1,5 kg"), ("masse", 1500.0))
        self.assertEqual(enheter.tolk_mengde("6"), ("antall", 6.0))
        self.assertIsNone(enheter.tolk_mengde("litt"))


class TestMatvarer(unittest.TestCase):
    def test_korte_ord_treffer_ikke_inni_andre_ord(self):
        self.assertTrue(matvarer.inneholder("Revet ost", "ost"))
        self.assertTrue(matvarer.inneholder("geitost", "ost"))
        self.assertFalse(matvarer.inneholder("biffstrimler", "ost"))
        self.assertTrue(matvarer.inneholder("2 egg", "egg"))
        self.assertEqual(matvarer.allergen_treff("Lammelegg", ["egg"]), [])

    def test_allergener(self):
        self.assertEqual(matvarer.allergen_treff("Bertagni Gnocchi", ["gluten"]), ["gluten"])
        self.assertEqual(matvarer.allergen_treff("TINE Kremfløte", ["laktose"]), ["laktose"])
        self.assertEqual(matvarer.allergen_treff("Kokosmelk 400 ml", ["laktose"]), [])
        self.assertEqual(matvarer.allergen_treff("Satay-saus", ["peanøtter"]), ["peanøtter"])

    def test_holdbarhet(self):
        self.assertEqual(matvarer.holdbarhet("Laks"), 2)
        self.assertTrue(matvarer.er_fersk("Bladpersille"))
        self.assertFalse(matvarer.er_fersk("Knuste tomater", "Mutti Knuste tomater Polpa"))
        self.assertEqual(matvarer.holdbarhet("Spisspaprika Nederland"), 7)  # "Nederland" er ikke and-kjøtt

    def test_frysbar(self):
        self.assertTrue(matvarer.er_frysbar("Gulasjsuppe med kjøttdeig"))
        self.assertTrue(matvarer.er_frysbar("Meksikansk gryte", ["Gryter"]))
        self.assertFalse(matvarer.er_frysbar("Sprø kylling med cæsarsalat", ["Salat"]))


class TestHandleliste(MedData):
    def test_samme_produkt_slas_sammen_paa_tvers_av_retter(self):
        # Stroganoff bruker 3 dl fløte, kylling/bacon-gryta 2 dl -> 5 dl = 2 kartonger, ikke 1+1 med mye rest
        liste = handleliste.beregn([(self.r(3004), 4), (self.r(5049), 4)], [], self.p)
        flote = [x for x in liste["kjop"] if x["produkt"]["id"] == 433]
        self.assertEqual(len(flote), 1)
        self.assertEqual(len(flote[0]["retter"]), 2)
        self.assertEqual(flote[0]["antall"], 2)

    def test_dobbel_porsjon_dobler_mengden(self):
        enkel = handleliste.beregn([(self.r(1966), 4)], [], self.p)
        dobbel = handleliste.beregn([(self.r(1966), 8)], [], self.p)
        behov = lambda l: sum(x["pakker_behov"] for x in l["kjop"])
        self.assertAlmostEqual(behov(dobbel), 2 * behov(enkel), places=1)

    def test_lager_sikker_og_usikker(self):
        lagervarer = [{"navn": "gul løk", "mengde": None}, {"navn": "pasta", "mengde": "500 g"}]
        liste = handleliste.beregn([(self.r(3004), 4), (self.r(2283), 4)], lagervarer, self.p)
        self.assertIn("Gul løk", [x["tittel"] for x in liste["fra_lager"]])
        self.assertNotIn(9215, [x["produkt"]["id"] for x in liste["kjop"]])
        usikre = {x["tittel"]: x for x in liste["usikre"]}
        self.assertIn("Fusilli, pasta", usikre)  # "pasta" ligner, men vi spør heller enn å anta

    def test_lager_med_mengde_trekkes_fra(self):
        # Stroganoff trenger 400 g kylling (pakke 500 g). Har vi 1 kg, kjøper vi ingen.
        lagervarer = [{"navn": "Kyllingfilet, strimlet", "mengde": "1 kg"}]
        liste = handleliste.beregn([(self.r(3004), 4)], lagervarer, self.p)
        self.assertNotIn("Kyllingfilet, strimlet", [x["tittel"] for x in liste["kjop"]])
        self.assertIn("Kyllingfilet, strimlet", [x["tittel"] for x in liste["fra_lager"]])

    def test_basisvarer(self):
        liste = handleliste.beregn([(self.r(3004), 4)], [], self.p)
        self.assertIn("Havsalt", [x["tittel"] for x in liste["basis_hjemme"]])
        self.assertIn("Paprikakrydder", [x["tittel"] for x in liste["usikre"]])

    def test_avklaring_styrer(self):
        liste = handleliste.beregn([(self.r(3004), 4)], [], self.p)
        nokkel, _ = handleliste.finn_nokkel(liste, "paprikakrydder")
        liste = handleliste.beregn([(self.r(3004), 4)], [], self.p, {nokkel: "kjop"})
        self.assertIn("Paprikakrydder", [x["tittel"] for x in liste["kjop"]])
        liste = handleliste.beregn([(self.r(3004), 4)], [], self.p, {nokkel: "har"})
        self.assertIn("Paprikakrydder", [x["tittel"] for x in liste["fra_lager"]])

    def test_ferskvarerester_rapporteres(self):
        liste = handleliste.beregn([(self.r(3004), 4)], [], self.p)
        self.assertIn("Seterrømme", [r["tittel"] for r in liste["rester"]])
        self.assertGreater(liste["svinn"], 0)


class TestPlan(MedData):
    def test_vanlig_uke_fem_retter(self):
        plan = planlegger.lag(self.oda, self.p, UKE)
        self.assertEqual([d["dag"] for d in plan["dager"]], ["man", "tir", "ons", "tor", "fre"])
        self.assertTrue(all(d["type"] == "lag" for d in plan["dager"]), plan["advarsler"])
        self.assertEqual(len({d["ref"] for d in plan["dager"]}), 5)
        self.assertEqual(plan["status"], "utkast")
        self.assertEqual(plan["dager"][0]["dato"], "2026-10-12")

    def test_aktivitetsdag_faar_rest_fra_dobbel_porsjon(self):
        plan = planlegger.lag(self.oda, self.p, UKE, ekstra_aktivitet=["tor"])
        dager = {d["dag"]: d for d in plan["dager"]}
        self.assertEqual(dager["ons"]["type"], "lag_dobbel")
        self.assertTrue(dager["ons"]["frysbar"])
        self.assertEqual(dager["tor"]["type"], "rest")
        self.assertEqual(dager["tor"]["ref"], dager["ons"]["ref"])
        self.assertEqual(dager["tor"]["lagring"], "kjøleskap")
        # Den doble retten kjøpes inn for 8 porsjoner
        retter = dict((r["ref"], n) for r, n in planlegger.retter(self.oda, plan))
        self.assertEqual(retter[dager["ons"]["ref"]], 8)

    def test_lang_avstand_gir_fryser(self):
        p = profil.sett("aktivitetsdager", "tir,fre")
        plan = planlegger.lag(self.oda, p, UKE)
        dager = {d["dag"]: d for d in plan["dager"]}
        self.assertEqual(dager["fre"]["type"], "rest")
        self.assertEqual(dager["tir"]["type"], "rest")
        self.assertEqual(dager["man"]["type"], "lag_dobbel")

    def test_aktivitet_forste_dag_gir_rask_rett(self):
        plan = planlegger.lag(self.oda, self.p, UKE, ekstra_aktivitet=["man"])
        man = plan["dager"][0]
        self.assertEqual(man["type"], "lag")
        self.assertLessEqual(man["minutter"], self.p["maks_tid_aktivitetsdag_min"])

    def test_ferdigmiddag_fra_fryseren_brukes_forst(self):
        lager.legg_til("kjøttsaus", fryst_middag_porsjoner=4)
        plan = planlegger.lag(self.oda, self.p, UKE, ekstra_aktivitet=["tor"])
        tor = [d for d in plan["dager"] if d["dag"] == "tor"][0]
        self.assertEqual(tor["type"], "ferdigmiddag")
        self.assertFalse(any(d["type"] == "lag_dobbel" for d in plan["dager"]))

    def test_allergi_filtrerer_bort_retter(self):
        p = profil.sett("allergier", "fisk,skalldyr")
        plan = planlegger.lag(self.oda, p, UKE)
        for r, _ in planlegger.retter(self.oda, plan):
            tekst = " ".join([r["navn"]] + [i["title"] for i in r["ingredienser"]])
            self.assertEqual(matvarer.allergen_treff(tekst, ["fisk", "skalldyr"]), [], r["navn"])

    def test_onskede_retter_brukes(self):
        plan = planlegger.lag(self.oda, self.p, UKE, onsket=["3004", "oda:1966"])
        refs = [d["ref"] for d in plan["dager"]]
        self.assertIn("oda:3004", refs)
        self.assertIn("oda:1966", refs)

    def test_nylig_brukte_retter_unngaas(self):
        forste = planlegger.lag(self.oda, self.p, UKE)
        planlegger._lagre_historikk(forste)
        andre = planlegger.lag(self.oda, self.p, "2026-W43")
        overlapp = {d["ref"] for d in forste["dager"]} & {d["ref"] for d in andre["dager"]}
        self.assertLessEqual(len(overlapp), 1)

    def test_bytt_og_dobbel_og_fri(self):
        plan = planlegger.lag(self.oda, self.p, UKE)
        planlegger.bytt(self.oda, plan, "man", "3004")
        self.assertEqual(plan["dager"][0]["ref"], "oda:3004")
        planlegger.bytt(self.oda, plan, "tir", "1966")
        planlegger.dobbel(plan, "tir", "fre")
        fre = plan["dager"][4]
        self.assertEqual((fre["type"], fre["ref"], fre["lagring"]), ("rest", "oda:1966", "fryser"))
        planlegger.fri(plan, "tir")
        self.assertEqual(plan["dager"][4]["type"], "mangler")
        with self.assertRaises(ValueError):
            planlegger.dobbel(plan, "fre", "man")


class TestKurvflyt(MedData):
    def _klar_plan(self):
        plan = planlegger.lag(self.oda, self.p, UKE)
        for u in planlegger.handleliste_for(self.oda, plan, self.p)["usikre"]:
            planlegger.avklar(self.oda, plan, self.p, u["tittel"], "har")
        return plan

    def test_ikke_i_kurv_uten_godkjenning(self):
        plan = self._klar_plan()
        with self.assertRaises(ValueError):
            planlegger.til_kurv(self.oda, plan, self.p)
        self.assertEqual(self.oda.lagt_til, [])

    def test_godkjenning_krever_avklaring(self):
        plan = planlegger.lag(self.oda, self.p, UKE)
        if planlegger.handleliste_for(self.oda, plan, self.p)["usikre"]:
            with self.assertRaises(ValueError):
                planlegger.godkjenn(self.oda, plan, self.p)

    def test_full_flyt(self):
        plan = self._klar_plan()
        planlegger.godkjenn(self.oda, plan, self.p)
        forhand = planlegger.kurvplan(self.oda, plan, self.p)
        self.assertEqual(self.oda.lagt_til, [], "forhåndsvisning skal ikke legge noe i kurven")
        res = planlegger.til_kurv(self.oda, plan, self.p)
        self.assertEqual(len(self.oda.lagt_til), len(forhand["linjer"]))
        self.assertEqual(res["feil"], [])
        self.assertEqual(planlegger.last()["status"], "i_kurv")
        with self.assertRaises(ValueError):  # aldri to ganger
            planlegger.til_kurv(self.oda, plan, self.p)
        self.assertIn(UKE, json.loads((Path(os.environ["MIDDAG_DATA"]) / "historikk.json").read_text()))

    def test_endring_etter_godkjenning_krever_ny(self):
        plan = self._klar_plan()
        planlegger.godkjenn(self.oda, plan, self.p)
        planlegger.bytt(self.oda, plan, "man", "3004")
        self.assertFalse(planlegger.er_godkjent(plan))
        with self.assertRaises(ValueError):
            planlegger.til_kurv(self.oda, plan, self.p)

    def test_trekk_fra_kurv(self):
        plan = self._klar_plan()
        planlegger.godkjenn(self.oda, plan, self.p)
        forste = planlegger.kurvplan(self.oda, plan, self.p)["linjer"][0]
        pid = forste["linje"]["produkt"]["id"]
        self.oda.kurv_innhold[pid] = 50
        kp = planlegger.kurvplan(self.oda, plan, self.p, trekk_fra_kurv=True)
        self.assertNotIn(pid, [l["linje"]["produkt"]["id"] for l in kp["linjer"]])
        kp = planlegger.kurvplan(self.oda, plan, self.p)
        self.assertEqual([l for l in kp["linjer"] if l["linje"]["produkt"]["id"] == pid][0]["allerede_i_kurv"], 50)

    def test_ikke_innlogget_rorer_ikke_kurven(self):
        plan = self._klar_plan()
        planlegger.godkjenn(self.oda, plan, self.p)
        self.oda.innlogget = False
        with self.assertRaises(OdaFeil):
            planlegger.til_kurv(self.oda, plan, self.p)
        self.assertEqual(self.oda.lagt_til, [])
        self.assertEqual(planlegger.last()["status"], "godkjent")

    def test_alt_feiler_beholder_godkjent_status(self):
        plan = self._klar_plan()
        planlegger.godkjenn(self.oda, plan, self.p)
        self.oda.feil_for = {x["linje"]["produkt"]["id"] for x in planlegger.kurvplan(self.oda, plan, self.p)["linjer"]}
        res = planlegger.til_kurv(self.oda, plan, self.p)
        self.assertEqual(res["lagt"], [])
        self.assertEqual(planlegger.last()["status"], "godkjent")

    def test_delvis_feil_kan_proves_igjen_uten_dobling(self):
        plan = self._klar_plan()
        planlegger.godkjenn(self.oda, plan, self.p)
        linjer = planlegger.kurvplan(self.oda, plan, self.p)["linjer"]
        feilet = linjer[0]["linje"]["produkt"]["id"]
        self.oda.feil_for = {feilet}
        planlegger.til_kurv(self.oda, plan, self.p)
        self.assertEqual(plan["status"], "delvis_i_kurv")
        self.oda.feil_for = set()
        self.oda.lagt_til = []
        planlegger.til_kurv(self.oda, plan, self.p)
        self.assertEqual([pid for pid, _ in self.oda.lagt_til], [feilet])
        self.assertEqual(plan["status"], "i_kurv")

    def test_sikre_innlogget_logger_inn_ved_gjest(self):
        tilstand = {"inne": False, "kall": []}

        def kjor(args, stdin=None):
            tilstand["kall"].append(args[:2])
            if args[:2] == ["auth", "login"]:
                tilstand["inne"] = True
                return 0, "", "Successfully logged in as: Ole"
            if args[:2] == ["auth", "user"]:
                return 0, "", "Logged in as: Ole" if tilstand["inne"] else "Not logged in."
            return 0, "{}", ""

        with tempfile.TemporaryDirectory() as d:
            fil = Path(d) / "oda.env"
            fil.write_text("ODA_EMAIL=a@b.no\nODA_PASSWORD=x\n")
            fil.chmod(0o600)
            os.environ["MIDDAG_CREDENTIALS"] = str(fil)
            try:
                Oda(kjor=kjor).sikre_innlogget()
            finally:
                os.environ.pop("MIDDAG_CREDENTIALS")
        self.assertEqual(tilstand["kall"], [["auth", "user"], ["auth", "login"], ["auth", "user"]])

    def test_ferdig_legger_rester_i_lager(self):
        plan = self._klar_plan()
        planlegger.ferdig(self.oda, plan, self.p)
        self.assertTrue(any("rest fra" in (v.get("notat") or "") for v in lager.last()))

    def test_avklar_har_husker_langholdbare(self):
        plan = planlegger.lag(self.oda, self.p, UKE, onsket=["3004"])
        planlegger.avklar(self.oda, plan, self.p, "paprikakrydder", "har")
        self.assertIn("Paprikakrydder", [v["navn"] for v in lager.last()])


class TestEgneOppskrifter(MedData):
    def test_lag_bruk_og_synk(self):
        e = oppskrifter.lag_egen("Fredagstaco", 4, ["Kjøttdeig=1234:1:Gilde Kjøttdeig 400 g", "Tacoskjell=555:1", "Salt"], "Stek kjøttet.", 25)
        self.assertEqual(e["slug"], "fredagstaco")
        self.assertFalse(e["frysbar"])
        plan = planlegger.lag(self.oda, self.p, UKE, onsket=["egen:fredagstaco"])
        self.assertIn("egen:fredagstaco", [d["ref"] for d in plan["dager"]])
        liste = planlegger.handleliste_for(self.oda, plan, self.p)
        self.assertIn(1234, [x["produkt"]["id"] for x in liste["kjop"]])
        oppskrifter.synk_egen(self.oda, "fredagstaco")
        self.assertEqual(self.oda.lister_laget, [("Fredagstaco", "Stek kjøttet.")])
        self.assertEqual(sorted(p for _, p, _ in self.oda.liste_varer), [555, 1234])
        with self.assertRaises(ValueError):
            oppskrifter.synk_egen(self.oda, "fredagstaco")


class TestOdaSikkerhet(unittest.TestCase):
    def setUp(self):
        self.kall = []
        self._tmp = tempfile.TemporaryDirectory()
        self.fil = Path(self._tmp.name) / "oda.env"
        os.environ["MIDDAG_CREDENTIALS"] = str(self.fil)

    def tearDown(self):
        self._tmp.cleanup()
        os.environ.pop("MIDDAG_CREDENTIALS", None)

    def _oda(self, svar=None):
        def kjor(args, stdin=None):
            self.kall.append((args, stdin))
            return svar(args) if svar else (0, "{}", "")
        return Oda(kjor=kjor)

    def test_farlige_kommandoer_er_sperret(self):
        oda = self._oda()
        for args in (["cart", "clear"], ["cart", "remove", "1"], ["slot", "select", "1"], ["recipe", "add", "1"],
                     ["list", "delete", "1"], ["list", "add-to-cart", "1"], ["checkout"], ["order", "place"]):
            with self.assertRaises(IkkeTillatt, msg=args):
                oda._kall(args)
        self.assertEqual(self.kall, [])

    def test_ingen_bestillingsmetode_finnes(self):
        for navn in dir(Oda):
            self.assertNotRegex(navn.lower(), "bestill|checkout|order|betal|pay|purchase")

    def test_passord_via_stdin_ikke_argument(self):
        self.fil.write_text("ODA_EMAIL=ole@example.com\nODA_PASSWORD=hemmelig123\n")
        self.fil.chmod(0o600)
        self._oda().logg_inn()
        args, stdin = self.kall[0]
        self.assertNotIn("hemmelig123", " ".join(args))
        self.assertIn("--pass-stdin", args)
        self.assertEqual(stdin.strip(), "hemmelig123")

    def test_nekter_lesbar_legitimasjonsfil(self):
        self.fil.write_text("ODA_EMAIL=a@b.no\nODA_PASSWORD=x\n")
        self.fil.chmod(0o644)
        with self.assertRaisesRegex(OdaFeil, "chmod 600"):
            self._oda().logg_inn()

    def test_logger_inn_paa_nytt_ved_utlopt_sesjon(self):
        self.fil.write_text("ODA_EMAIL=a@b.no\nODA_PASSWORD=x\n")
        self.fil.chmod(0o600)
        teller = {"n": 0}

        def svar(args):
            if args[0] == "cart":
                teller["n"] += 1
                if teller["n"] == 1:
                    return 1, "", "Get cart failed: HTTP 401 (authentication may be required or expired)"
                return 0, '{"items": []}', ""
            return 0, "", "Successfully logged in"

        self.assertEqual(self._oda(svar).kurv(), {"items": []})
        self.assertEqual([a[0][:2] for a in self.kall], [["cart", "list"], ["auth", "login"], ["cart", "list"]])


class TestProfil(MedData):
    def test_porsjoner_for_familien(self):
        self.assertEqual(profil.porsjoner(self.p), 4)  # 1 + 1 + 0,7 + 0,5 = 3,2 -> 4

    def test_endringer(self):
        profil.legg_til("allergier", ["nøtter"])
        profil.legg_til("aktivitetsdager", ["Tirsdag", "tor"])
        p = profil.fjern("aktivitetsdager", ["tirsdag"])
        self.assertEqual(p["allergier"], ["nøtter"])
        self.assertEqual(p["aktivitetsdager"], ["tor"])
        self.assertEqual(profil.sett("porsjoner", "5")["porsjoner"], 5)
        with self.assertRaises(ValueError):
            profil.sett("aktivitetsdager", "blursdag")


if __name__ == "__main__":
    unittest.main()
