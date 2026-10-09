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

from middagslib import enheter, handleliste, lager, matvarer, oppskrifter, planlegger, profil, rapport  # noqa: E402
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
        self.kurv_overstyring = {}
        self.lagt_til = []
        self.lister_laget = []
        self.produkter = {}
        self.liste_varer = []

    def sok_oppskrift(self, sok=None, side=1, filtre=("meal:65",)):
        fil = FIX / f"search_{sok}.json"
        if fil.exists():
            return json.loads(fil.read_text())
        return {"items": []}

    def sok_produkt(self, sok, side=1):
        return {"items": self.produkter.get(sok, [])}

    def oppskrift_detaljer(self, oid):
        return {"name": "x", "description": "En god rett.", "ingredients": [], "instructions": ["Stek kjøttet.", "Server."]}

    def oppskrift(self, oid):
        return fixture_oppskrift(oid)

    def kurv(self):
        items = [{"id": k, "quantity": v} for k, v in self.kurv_innhold.items()]
        return {"items": items, "product_quantity_count": sum(self.kurv_innhold.values()), "display_price": 0} | self.kurv_overstyring

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
        # Summer over kjop + usikre: en liten mengde kan krysse spørre-grensen og
        # flytte seg mellom kategoriene når porsjonene dobles (f.eks. Karve).
        behov = lambda l: sum(x["pakker_behov"] for x in l["kjop"]) + sum(x["pakker_behov"] for x in l["usikre"])
        self.assertAlmostEqual(behov(dobbel), 2 * behov(enkel), places=1)

    def test_liten_mengde_krydder_sporres_ikke_autokjopes(self):
        # Karve brukes med 6 % av pakken og er ikke flagget is_basic av Oda -- skal
        # spørres om, ikke kjøpes blindt (jf. kanel-saken: hele pakker for en klype).
        liste = handleliste.beregn([(self.r(1966), 4)], [], self.p)
        self.assertNotIn("Karve", [x["tittel"] for x in liste["kjop"]])
        usikre = {x["tittel"]: x for x in liste["usikre"]}
        self.assertIn("Karve", usikre)
        self.assertIn("% av pakken", usikre["Karve"]["grunn"])
        self.assertIn("Kjøttdeig, storfe", [x["tittel"] for x in liste["kjop"]])

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

    def test_gammel_ferskvare_gir_sporsmal(self):
        lagervarer = [{"navn": "Bladpersille", "mengde": None, "lagt_til": "2020-01-01"}]
        liste = handleliste.beregn([(self.r(3004), 4)], lagervarer, self.p)
        self.assertIn("fortsatt bra", [u for u in liste["usikre"] if u["tittel"] == "Bladpersille"][0]["grunn"])
        lagervarer[0]["lagt_til"] = __import__("datetime").date.today().isoformat()
        liste = handleliste.beregn([(self.r(3004), 4)], lagervarer, self.p)
        self.assertIn("Bladpersille", [x["tittel"] for x in liste["fra_lager"]])

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

    def test_mandag_tirsdag_aktivitet_fryser_til_neste_uke(self):
        p = profil.sett("aktivitetsdager", "man,tir")
        plan = planlegger.lag(self.oda, p, UKE)
        dager = {d["dag"]: d for d in plan["dager"]}
        for d in ("man", "tir"):
            self.assertEqual(dager[d]["type"], "lag")
            self.assertLessEqual(dager[d]["minutter"], p["maks_tid_aktivitetsdag_min"])
        frys = [d for d in plan["dager"] if d.get("frys_til")]
        self.assertEqual(sorted(d["frys_til"] for d in frys), ["man", "tir"])
        self.assertTrue(all(d["type"] == "lag_dobbel" and d["frysbar"] for d in frys))
        # Uken etter: ferdigmiddagene fra fryseren brukes mandag og tirsdag
        plan["status"] = "i_kurv"
        planlegger.ferdig(self.oda, plan, p)
        neste = planlegger.lag(self.oda, p, "2026-W43")
        nd = {d["dag"]: d for d in neste["dager"]}
        self.assertEqual((nd["man"]["type"], nd["tir"]["type"]), ("ferdigmiddag", "ferdigmiddag"))

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

    def test_lavt_belop_gir_ekstra_middag_i_fryseren(self):
        plan = planlegger.lag(self.oda, self.p, UKE)
        self.assertEqual(plan["ekstra_middager"], [])  # vanlig uke er godt over minstebeløpet

        p = profil.sett("min_bestilling_kr", "100000")
        plan = planlegger.lag(self.oda, p, UKE)
        self.assertTrue(plan["ekstra_middager"])
        self.assertTrue(all(x["frysbar"] for x in plan["ekstra_middager"]))
        liste = planlegger.handleliste_for(self.oda, plan, p)
        self.assertGreaterEqual(liste["sum"], 700)  # ikke nok til å nå 100000, men flere retter enn uten
        refs_i_handleliste = {r["ref"] for r, _ in planlegger.retter(self.oda, plan)}
        for x in plan["ekstra_middager"]:
            self.assertIn(x["ref"], refs_i_handleliste)
        self.assertIn("ekstra middag", plan["advarsler"][0].lower())

    def test_minstebelop_tar_med_faste_varer(self):
        # Faste ukevarer (bleier, brød osv.) legges også i kurven og teller mot
        # minstebeløpet -- uten dette ble det lagt til unødvendige ekstra middager.
        p = profil.sett("min_bestilling_kr", "1500")
        p = profil.fast_vare(99999, "Testvare", antall=1, pris=200.0)
        plan = planlegger.lag(self.oda, p, UKE)
        self.assertEqual(plan["ekstra_middager"], [])

    def test_minstebelop_bruker_rabattert_belop_i_kurven(self):
        # Oda sin display_price kan se høyere ut enn det som faktisk belastes,
        # fordi mengderabatt ("2 for 1" osv.) ikke trekkes fra der. Sjekken skal
        # bruke det rabatterte beløpet, ikke display_price direkte.
        p = profil.sett("min_bestilling_kr", "2500")
        self.oda.kurv_overstyring = {"display_price": 99999, "discounted_display_price": 0}
        plan = planlegger.lag(self.oda, p, UKE)
        self.assertTrue(plan["ekstra_middager"])

    def test_ferdig_legger_ekstra_middag_i_fryseren(self):
        p = profil.sett("min_bestilling_kr", "100000")
        plan = planlegger.lag(self.oda, p, UKE)
        plan["status"] = "i_kurv"
        for u in planlegger.handleliste_for(self.oda, plan, p)["usikre"]:
            planlegger.avklar(self.oda, plan, p, u["tittel"], "har")
        meldinger = planlegger.ferdig(self.oda, plan, p)
        for x in plan["ekstra_middager"]:
            self.assertTrue(any(x["navn"] in m for m in meldinger))
        fryste_navn = {v["navn"] for v in lager.ferdigmiddager()}
        for x in plan["ekstra_middager"]:
            self.assertIn(x["navn"], fryste_navn)


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

    def test_ekstravarer_gaar_i_kurven_og_krever_ny_godkjenning(self):
        plan = self._klar_plan()
        planlegger.godkjenn(self.oda, plan, self.p)
        planlegger.ekstra(plan, 7597, "Knorr Hønsebuljong", 2, 22.72)
        self.assertFalse(planlegger.er_godkjent(plan))
        planlegger.godkjenn(self.oda, plan, self.p)
        planlegger.til_kurv(self.oda, plan, self.p)
        self.assertIn((7597, 2), self.oda.lagt_til)

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


def _treff(pid, navn, storrelse, pris, tilgjengelig=True):
    return {"id": pid, "name": navn, "subtitle": storrelse, "price": pris, "availability": {"is_available": tilgjengelig, "code": "", "description": ""}}


class TestBilligst(MedData):
    def setUp(self):
        super().setUp()
        self.oda.produkter["kremfløte"] = [
            _treff(901, "Q Kremfløte", "3 dl", 21.90),
            _treff(902, "Laktosefri Kremfløte", "3 dl", 15.00),   # annen variant
            _treff(903, "Kremfløte", "1 l", 49.90),               # billigere per liter, men dyrere for behovet
            _treff(904, "Kremfløte", "3 dl", 9.90, False),        # utsolgt
            _treff(906, "Kokoskremfløte", "3 dl", 8.00),          # annet sammensatt ord
        ]

    def test_bytter_til_rimeligste_likeverdige(self):
        plan = planlegger.lag(self.oda, self.p, UKE, onsket=["3004"])
        b = [b for b in plan["bytter"].values() if b["tittel"] == "Kremfløte"]
        self.assertEqual(len(b), 1)
        self.assertIn("Q Kremfløte", b[0]["til"])
        self.assertAlmostEqual(b[0]["spart"] % 6.0, 0.0, places=1)  # 6 kr spart per kartong
        liste = planlegger.handleliste_for(self.oda, plan, self.p)
        self.assertIn(901, [x["produkt"]["id"] for x in liste["kjop"]])
        self.assertNotIn(433, [x["produkt"]["id"] for x in liste["kjop"]])

    def test_angre_bytte(self):
        plan = planlegger.lag(self.oda, self.p, UKE, onsket=["3004"])
        planlegger.original(self.oda, plan, self.p, "kremfløte")
        planlegger.optimaliser_priser(self.oda, plan, self.p)  # skal ikke bytte tilbake
        liste = planlegger.handleliste_for(self.oda, plan, self.p)
        self.assertIn(433, [x["produkt"]["id"] for x in liste["kjop"]])

    def test_allergi_stopper_bytte(self):
        self.oda.produkter["kremfløte"] = [_treff(905, "Kremfløte med nøtter", "3 dl", 10.0)]
        p = profil.sett("allergier", "nøtter")
        plan = planlegger.lag(self.oda, p, UKE, onsket=["3004"])
        self.assertFalse([b for b in plan["bytter"].values() if b["tittel"] == "Kremfløte"])


class TestUtskrift(MedData):
    def _plan(self):
        p = profil.sett("aktivitetsdager", "ons")
        plan = planlegger.lag(self.oda, p, UKE)
        plan["status"] = "i_kurv"
        planlegger.lagre(plan)
        return plan

    def test_innhold_for_dobbeldag_skalerer_og_minner_om_rest(self):
        from middagslib import utskrift
        plan = self._plan()
        tir = [d for d in plan["dager"] if d["dag"] == "tir"][0]
        self.assertEqual(tir["type"], "lag_dobbel")
        data = utskrift.innhold(self.oda, plan, tir)
        self.assertIn("8 porsjoner", data["undertittel"])
        self.assertTrue(any("dobbel" in m for m in data["merknader"]))
        self.assertEqual(data["steg"], ["Stek kjøttet.", "Server."])

    def test_ikke_godkjent_plan_skrives_ikke_ut(self):
        from middagslib import utskrift
        plan = planlegger.lag(self.oda, self.p, UKE)
        melding = utskrift.skriv_ut(self.oda, plan, __import__("datetime").date(2026, 10, 12), bare_fil=True)
        self.assertIn("ikke godkjent", melding)

    def test_lager_fil_og_hopper_over_restedag(self):
        import datetime as dt
        from middagslib import utskrift
        plan = self._plan()
        self.assertIn("Laget", utskrift.skriv_ut(self.oda, plan, dt.date(2026, 10, 12), bare_fil=True))
        self.assertIn("ingen oppskrift", utskrift.skriv_ut(self.oda, plan, dt.date(2026, 10, 14), bare_fil=True))
        self.assertIn("Ingen middag", utskrift.skriv_ut(self.oda, plan, dt.date(2026, 10, 17), bare_fil=True))


class TestLagerHoldbarhet(MedData):
    def _dager_siden(self, n):
        import datetime as dt
        return (dt.date.today() - dt.timedelta(days=n)).isoformat()

    def _sett(self, varer):
        lager.lagre(varer)

    def test_ferdig_trekker_fra_brukt_mengde_og_merker_ukjent(self):
        self._sett([
            {"navn": "Kyllingfilet, strimlet", "mengde": "1 kg", "lagt_til": self._dager_siden(1)},
            {"navn": "Gul løk", "mengde": None, "lagt_til": self._dager_siden(1)},
        ])
        plan = planlegger.lag(self.oda, self.p, UKE, onsket=["3004"])
        planlegger.ferdig(self.oda, plan, self.p)
        varer = {v["navn"]: v for v in lager.last()}
        dag = [d for d in plan["dager"] if d["ref"] == "oda:3004"][0]
        brukt_g = 400 * dag["porsjoner"] / 4 * (2 if dag["type"] == "lag_dobbel" else 1)
        self.assertEqual(varer["Kyllingfilet, strimlet"]["mengde"], enheter.vis("masse", 1000 - brukt_g))
        self.assertTrue(varer["Gul løk"]["sjekk"])
        with self.assertRaises(ValueError):
            planlegger.ferdig(self.oda, plan, self.p)

    def test_rydd_fjerner_bare_ferskvare_som_er_dobbelt_for_gammel(self):
        self._sett([
            {"navn": "rød paprika", "mengde": None, "lagt_til": self._dager_siden(15)},   # 7 d -> borte etter 14
            {"navn": "gul løk", "mengde": None, "lagt_til": self._dager_siden(30)},       # 21 d -> blir
            {"navn": "jasminris", "mengde": None, "lagt_til": self._dager_siden(400)},    # tørrvare -> blir
            {"navn": "kjøttsaus", "fryst_middag": True, "porsjoner": 4, "lagt_til": self._dager_siden(400)},
        ])
        fjernet = lager.rydd_utgatt()
        self.assertEqual([n for n, _ in fjernet], ["rød paprika"])
        self.assertEqual(sorted(v["navn"] for v in lager.last()), ["gul løk", "jasminris", "kjøttsaus"])

    def test_sjekk_og_ok(self):
        self._sett([
            {"navn": "rød paprika", "mengde": None, "lagt_til": self._dager_siden(8)},
            {"navn": "risnudler", "mengde": None, "lagt_til": self._dager_siden(1), "sjekk": True, "notat": "brukt i wok – sjekk"},
            {"navn": "gulrøtter", "mengde": None, "lagt_til": self._dager_siden(2)},
        ])
        self.assertEqual(sorted(n for n, _ in lager.til_sjekk()), ["risnudler", "rød paprika"])
        self.assertIn("Lagersjekk", lager.sjekkmelding([], lager.til_sjekk()))
        lager.ok("rød paprika")
        lager.ok("risnudler")
        self.assertEqual(lager.til_sjekk(), [])
        self.assertEqual(lager.sjekkmelding([], []), "")


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


class TestOdaRabatt(unittest.TestCase):
    def test_bruker_rabattert_belop_naar_tilgjengelig(self):
        kurv = {"display_price": 1350.9, "discounted_display_price": 1275.5}
        self.assertEqual(Oda.belop_etter_rabatt(kurv), 1275.5)

    def test_faller_tilbake_til_display_price_uten_rabattfelt(self):
        self.assertEqual(Oda.belop_etter_rabatt({"display_price": 500}), 500)


class TestRapportRabatt(unittest.TestCase):
    def test_kurv_for_viser_rabattert_belop_og_advarsel(self):
        # display_price (1350.9) ser ut til å nå grensen (1300), men etter Odas
        # egen mengderabatt ("2 for 1") er reell sum 1275.5 -- under grensen.
        kp = {
            "linjer": [], "hoppet_over": [],
            "kurv_for": {"display_price": 1350.9, "discounted_display_price": 1275.5,
                         "product_quantity_count": 36, "items": []},
        }
        tekst = rapport.kurv_for(kp, {"min_bestilling_kr": 1300})
        self.assertIn("før mengderabatt", tekst)
        self.assertIn("tillegg for mindre bestillinger", tekst)

    def test_kurv_for_uten_rabatt_viser_ingen_notis(self):
        kp = {
            "linjer": [], "hoppet_over": [],
            "kurv_for": {"display_price": 1400, "product_quantity_count": 30, "items": []},
        }
        tekst = rapport.kurv_for(kp, {"min_bestilling_kr": 1300})
        self.assertNotIn("før mengderabatt", tekst)

    def test_kurv_etter_viser_rabattert_belop_og_advarsel(self):
        res = {
            "lagt": [], "feil": [], "hoppet_over": [],
            "kurv_for": {"display_price": 1000, "product_quantity_count": 30, "items": []},
            "kurv_etter": {"display_price": 1350.9, "discounted_display_price": 1275.5,
                           "product_quantity_count": 36, "items": []},
        }
        tekst = rapport.kurv_etter(res, {"min_bestilling_kr": 1300})
        self.assertIn("før mengderabatt", tekst)
        self.assertIn("tillegg for mindre bestillinger", tekst)


if __name__ == "__main__":
    unittest.main()
