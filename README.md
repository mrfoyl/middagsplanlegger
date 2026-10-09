# Middagsplanlegger

Ukentlig middagsplan for familien, med handlekurven fylt hos Oda.
**Den bestiller og betaler aldri.** Den legger bare varer i kurven, og du trykker
«bestill» selv i Oda.

Agenten (Kølla) kaller den på WhatsApp på samme måte som `listonic.py`. Hvordan
agenten skal bruke den står i [KOLLA.md](KOLLA.md).

## Flyt

```
plan lag  →  plan vis  →  (avklar / bytt / dobbel / fri)  →  plan godkjenn  →  plan kurv --utfor
                                                         viser «før»            viser «etter»
```

1. `plan lag` velger retter for neste uke. Den tar hensyn til familieprofilen,
   allergier, aktivitetsdager, lageret og hva dere har spist de siste tre ukene.
2. Ukens ingredienser slås sammen for alle retter samlet. Oda oppgir
   hvor mange *pakker* hver porsjon trenger, og vi summerer brøkdelene per vare før
   vi runder opp. Er dere usikre på om dere har noe hjemme, spør den i stedet for
   å gjette.
3. Utvalget skjer én rett om gangen. Hver ny rett vurderes etter hva den gjør med
   hele ukens handleliste: ekstra kostnad, ferskvare som blir til overs, og
   variasjon.
4. Frysbare retter (gryter, supper, lasagne …) lages i dobbel porsjon en vanlig
   dag, og resten spises på en aktivitetsdag. Er det 1–2 dager mellom, holder
   kjøleskapet. Ellers fryses resten. Ferdigmiddager som allerede ligger i
   fryseren, brukes først.
5. For hver vare søker den etter rimeligste likeverdige produkt
   (`middagslib/billigst.py`). Den sammenligner hva ukens behov koster i hele
   pakker, ikke bare kilopris. Den bytter bare når hovedordet stemmer, varen ikke
   er en annen variant (røkt, saus, laktosefri, kylling- i stedet for vanlig …)
   og ingen allergier treffes. Byttene vises i planen og kan angres med
   `plan original <vare>`.
6. Ingenting legges i kurven før planen er godkjent. Endrer du planen etter
   godkjenning, må den godkjennes på nytt. Samme plan kan ikke legges i
   kurven to ganger.

## Sikkerhet

- **Aldri bestilling.** mcp-oda har ingen bestill- eller betal-kommando. I tillegg
  sperrer `middagslib/oda.py` alt som ikke står på en hviteliste: tømme kurven,
  fjerne varer, velge leveringstid, slette lister og legge hele oppskrifter i
  kurven forbi lagersjekken. Dette er dekket av tester.
- **Innlogging.** E-post og passord ligger i `~/.config/middag/oda.env` med
  rettighet 600, utenfor repoet. Programmet nekter å lese filen hvis andre kan
  lese den. Passordet sendes til mcp-oda via stdin (`--pass-stdin`), aldri som
  argument, så det vises ikke i `ps`. Selve sesjonen (cookies) ligger i
  `~/.mcp-oda/`.
- **Ikke gjestekurv.** Uten innlogging gir Oda en anonym kurv. Derfor bekreftes
  innloggingen før kurven leses eller endres.

## Oppsett (Pi-en / openclaw)

Krever Node 18+ og Python 3.10+. Ingen Python-pakker trengs.

```bash
# kopier repoet til Pi-en (uten data/ og vendor/)
rsync -a --exclude data --exclude vendor middagsplanlegger/ openclaw:~/.openclaw/workspace/middagsplanlegger/

ssh openclaw
cd ~/.openclaw/workspace/middagsplanlegger
scripts/setup_oda.sh          # kloner mcp-oda, legger på patcher, bygger
scripts/sett_oda_passord.sh   # spør om e-post/passord, skriver oda.env (600)
python3 middag.py oda login   # tester innloggingen
python3 tests/test_middag.py  # 63 tester, uten nettverk
```

Valgfritt: kalenderen. Den bruker `gcalendar.py` i openclaw-workspace:

```bash
python3 middag.py profil sett kalender ja
python3 middag.py profil sett kalender_sokeord "fotball,svømming,turn"
```

### Utskrift av dagens oppskrift

`python3 middag.py skriv-ut` skriver ut dagens oppskrift som en A4-PDF. Arket har
ingredienser regnet om til porsjonene som skal lages, fremgangsmåten,
produktbytter og påminnelser som «lag dobbel» og «ta ut fra fryseren i kveld».
Utskriften går via CUPS (`lp`) til skriveren i profilen. Standard er
`Brother-HL-L2400DW`, og den endres med `profil sett skriver <kø>`. Hver dag
skrives bare ut én gang. Restedager og ferdigmiddager hoppes over.

Krever `python3-reportlab` (finnes allerede på Pi-en). Uten den skrives en
ren tekstversjon ut.

Cron på Pi-en, hver ukedag kl. 07:00:

```cron
0 7 * * 1-5 cd ~/.openclaw/workspace/middagsplanlegger && python3 middag.py skriv-ut >> data/utskrift.log 2>&1
```

Annen dag eller manuelt: `skriv-ut --dag fre`, `--igjen`, eller `--bare-fil`
for bare å lage PDF-en.

### Lageret holdes ferskt

- **Forbruk:** Når uken avsluttes (`plan ferdig`, kjøres automatisk av neste
  `plan lag`), trekkes det rettene brukte fra lageret. Varer med kjent mengde
  reduseres, og varer uten mengde merkes ❓ «sjekk om noe er igjen».
- **Automatisk rydding:** Ferskvare som har ligget dobbelt så lenge som den
  holder seg, fjernes ved `plan lag` og `lager rydd`. Tørrvarer, krydder og
  ferdigmiddager i fryseren fjernes aldri automatisk.
- **Ukentlig sjekk:** `lager sjekk --send` sender en liste på WhatsApp over
  ferskvare som har passert holdbarheten og varer merket ❓. Svarene registreres
  med `lager ok <vare>` (finnes fortsatt, ny dato) eller `lager fjern <vare>`.
  Krever `profil sett whatsapp +47...`. Den sendes via
  `openclaw message send`, på samme måte som ukeoppsummeringen.

Cron på Pi-en, søndag kl. 18:00 før ukeplanen:

```cron
0 18 * * 0 cd ~/.openclaw/workspace/middagsplanlegger && python3 middag.py lager sjekk --send >> data/lagersjekk.log 2>&1
```

### Minstebeløp og mengderabatter

Oda sin egen kurv-sum (`display_price`) er bare summen av varelinjenes
ordinære pris. Den trekker **ikke** fra mengderabatter som «2 for 1» –
den rabatten trekkes først fra når ordren faktisk belastes. For varer med
en slik aktiv kampanje gir Oda også et eget felt
(`discounted_display_price_total` per linje) med det reelle beløpet, men
det blir borte i Odas vanlige kurv-API-sammendrag.

`vendor/mcp-oda` (patch `0002`) leser nå dette feltet og legger sammen
`discounted_display_price` for hele kurven. `middagslib/oda.py` sin
`Oda.belop_etter_rabatt(kurv)` bruker det rabatterte beløpet når det finnes,
og faller tilbake til `display_price` ellers. Både minstebeløp-sjekken i
`sikre_minstebelop()` og det Ole får se i `plan kurv`/`plan kurv --utfor`
bruker denne funksjonen, så planen ikke tror den er over grensen når den i
praksis ikke er det. Rapportene viser i tillegg Odas urabatterte tall i
parentes når de to avviker, så avviket er synlig.

### Miljøvariabler

| Variabel | Standard | Brukes til |
|---|---|---|
| `MIDDAG_DATA` | `./data` | profil, lager, plan, historikk, cache |
| `MIDDAG_CREDENTIALS` | `~/.config/middag/oda.env` | Oda-innlogging |
| `MIDDAG_ODA_CMD` | `node vendor/mcp-oda/dist/index.js` | kjøre mcp-oda på annen måte |
| `MIDDAG_ODA_DATA` | (mcp-oda: `~/.mcp-oda`) | sesjonskatalog |
| `MIDDAG_GCALENDAR_DIR` | `~/.openclaw/workspace` | hvor `gcalendar.py` ligger |

## Fork av mcp-oda

`vendor/mcp-oda` er en lokal fork av
[agfagerbakk/mcp-oda](https://github.com/agfagerbakk/mcp-oda), låst til commit
`62f0b64`, med vår patch i `patches/`:

- `recipe ingredients <id>`, også tilgjengelig som MCP-verktøyet
  `recipes_get_ingredients`. Den gir produkt-ID, pakker per porsjon og
  basisvare-flagg for hver ingrediens. `recipe details` gir bare fritekst, og
  det er ikke nok til å slå sammen varer på tvers av retter.
- Kurven (`cart list`) eksponerer også det reelle beløpet etter mengderabatter
  (`discounted_display_price` på kurven, `discounted_line_total` per vare).
  Se [Minstebeløp og mengderabatter](#minstebeløp-og-mengderabatter).

`scripts/setup_oda.sh` gjenskaper forken fra upstream og patchene. Slik
oppdaterer du upstream: endre `PIN`, slett `vendor/`, kjør skriptet på nytt og
løs eventuelle konflikter.

## Struktur

```
middag.py               CLI
middagslib/
  oda.py                hviteliste over mcp-oda, innlogging
  oppskrifter.py        Oda-oppskrifter (cache 14 d) + egne oppskrifter
  handleliste.py        sammenslåing på tvers av retter, lager, svinn
  planlegger.py         utvalg, dagfordeling, godkjenning, kurv
  profil.py / lager.py  familieprofil og det vi har hjemme
  matvarer.py           holdbarhet, allergener, frysbarhet (nøkkelord)
  enheter.py            «500 g», «3 dl», «4 x 125 g»
  kalender.py           aktivitetsdager fra Google-kalenderen (valgfritt)
  rapport.py            tekst for WhatsApp
  utskrift.py           PDF av dagens oppskrift til skriveren
tests/                  tester mot ekte Oda-oppskriftsdata i fixtures/
```

## Begrensninger

- Allergenfilteret leter etter nøkkelord, for eksempel «fløte» for melk og
  «pasta» for gluten. Det fanger det vanlige, men er ingen garanti. Har noen en
  alvorlig allergi, må ingredienslisten sjekkes (`oppskrift vis <ref>`).
- Holdbarhet og frysbarhet er grove anslag ut fra navnet. Om en rett tåler
  frysing kan overstyres med `oppskrift frysbar <ref> ja|nei`.
- Lagervarer uten oppgitt mengde regnes som «nok». Varer som bare ligner,
  for eksempel «pasta» mot «fusilli», gir alltid et spørsmål.
