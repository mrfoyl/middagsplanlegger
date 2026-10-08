# Middagsplanlegger – instruks for Kølla

Verktøy: `python3 ~/.openclaw/workspace/middagsplanlegger/middag.py <kommando>`.
Utskriften er WhatsApp-formatert og kan sendes rett til Ole.

## Absolutte regler

1. **Aldri bestill eller betal.** Verktøyet kan ikke, og du skal ikke prøve med
   andre midler, heller ikke via mcp-oda direkte. Ole trykker «bestill» selv.
2. **Ikke legg noe i kurven før Ole har sett planen og sagt ja.** `plan kurv --utfor` kjøres
   bare etter at Ole eksplisitt har godkjent planen i denne samtalen.
3. **Spør heller enn å gjette** om hva som finnes hjemme. Svar på `❓ Må avklares`
   med det Ole sier, ikke med antagelser.
4. Allergier i profilen er alvorlige. Allergenfilteret leter etter nøkkelord, så
   les ingredienslisten (`oppskrift vis <ref>`) før du foreslår en rett du er
   usikker på.

## Ukesflyt

1. `plan lag`. Uke-planen for neste uke. Valgfritt:
   - `--aktivitet tir,tor` for ekstra aktivitetsdager denne uken
   - `--oppskrifter oda:3004,egen:taco` hvis Ole ønsker bestemte retter
   - `--uke 2026-W43` for en annen uke
2. Send planen til Ole. Gjør endringene Ole ber om:
   - `plan bytt <dag> <ref>` setter en annen rett. `oppskrift sok <ord>` gir
     forslag.
   - `plan dobbel <kokedag> <restdag>` lager dobbel porsjon og legger resten på
     en annen dag.
   - `plan fri <dag>` betyr ingen middag den dagen.
   - `plan ferdigmiddag <dag> <navn>` bruker noe fra fryseren.
   - `plan avklar <vare> har|kjop` svarer på spørsmålene under «❓».
   - `plan erstatt <vare> <produkt-id> --navn "..." --pris 39.90` velger et
     annet produkt. Finn ID med `produkt sok <ord>`.
   - `plan ekstra <produkt-id> [antall] --navn "..." --pris 22.72` legger til varer
     som ikke hører til en rett, for eksempel «vi trenger mer kyllingbuljong». Finn
     ID-en med `produkt sok`. `plan ekstra-fjern <produkt-id>` tar varen ut igjen.
   - Planleggeren bytter selv til rimeligste likeverdige vare (samme type, ingen
     annen variant, sammenlignet på faktisk behov). Byttene står under «Byttet til
     rimeligere». Vil Ole ha originalen: `plan original <vare>`. `plan billigst`
     kjører prissjekken på nytt.
3. Når Ole sier at planen ser bra ut: `plan godkjenn`. Det viser hva som legges
   i kurven («før»). Send det til Ole.
4. Når Ole sier «legg i kurven» eller lignende: `plan kurv --utfor`. Send «etter»-
   oppsummeringen.
   - Ligger noen varer allerede i kurven og Ole vil unngå dobbelt opp, bruk
     `plan kurv --utfor --trekk-fra-kurv`.
   - Hvis noe feilet: kjør samme kommando igjen. Det som allerede ble lagt til,
     hoppes over.
5. Når uken er over, eller før neste plan: `plan ferdig`. Rester av det som ble
   kjøpt, legges i lageret.

Endrer du noe etter godkjenning, må planen godkjennes på nytt. Verktøyet
håndhever det.

## Lager: det vi har hjemme

- Ole skriver «vi har melk, 1 kg kjøttdeig og løk»:
  `lager legg-til "melk" "kjøttdeig=1 kg" "løk"`
- Ole sender et **bilde** av kjøleskapet, skapet eller en kvittering: les selv
  hvilke varer du ser, og legg dem til med mengde der den er tydelig. Si hva
  du la til, og hva du var usikker på. Gjett ikke på uleselige varer, spør.
- Ole skriver «vi er tomme for melk»: `lager fjern melk`
- Middag i fryseren: `lager legg-til "kjøttsaus" --fryst-middag 4`
  (antall porsjoner). Den brukes automatisk på en aktivitetsdag.
- `lager vis` viser hele lageret.

## Familieprofil

`profil vis`. Endres når Ole sier fra:

| Ole sier | Kommando |
|---|---|
| «Jente har nøtteallergi» | `profil legg-til allergier nøtter` |
| «Vi vil ikke ha sopp» | `profil legg-til unngaa sopp` |
| «Ungene elsker taco» | `profil legg-til liker taco` |
| «Fotball tirsdag og torsdag» | `profil sett aktivitetsdager tir,tor` |
| «Middag også i helgene» | `profil sett middagsdager man,tir,ons,tor,fre,lør,søn` |
| «Maks 30 min på hverdager» | `profil sett maks_tid_min 30` |
| «Gutten har fylt 7» | `profil medlem-alder 3 7` (medlem nr. 3) |
| «Lag til 5 porsjoner» | `profil sett porsjoner 5` (`auto` for beregnet) |
| «Hent aktiviteter fra kalenderen» | `profil sett kalender ja` + `profil sett kalender_sokeord "fotball,turn"` |
| «Vi har alltid soyasaus» | `profil legg-til alltid_hjemme soyasaus` |

Allerginøkler som forstås: gluten, melk/laktose, egg, nøtter, peanøtter, fisk,
skalldyr, soya, sesam, selleri, sennep. Alt annet matches som fritekst.

## Egne oppskrifter

Når Ole beskriver en rett som ikke finnes hos Oda:

1. Finn produktene: `produkt sok kjøttdeig` osv.
2. `egen "Fredagstaco" --porsjoner 4 --minutter 25 --instruksjoner "1. Stek ... 2. ..."
   --ingrediens "Kjøttdeig=12345:1" --ingrediens "Tacoskjell=678:1" --ingrediens "Salt"`
   - Formatet er `Tittel=produkt-ID:pakker for hele oppskriften`. Bare tittel
     betyr basisvare.
   - Legg til `--frysbar` hvis retten tåler frysing.
3. Valgfritt, og bare hvis Ole vil ha den i Oda-appen: `oppskrift egen-synk egen:fredagstaco`.
   Da lagres den under Oppskrifter → Dine middager.
4. Bruk den i en plan: `plan lag --oppskrifter egen:fredagstaco` eller `plan bytt fre egen:fredagstaco`.

## Feil

- «Mangler …/oda.env» eller «Innlogging feilet»: be Ole kjøre
  `scripts/sett_oda_passord.sh` på Pi-en. Be aldri om passordet i chatten.
- «rettighet 0o644»: kjør `chmod 600 ~/.config/middag/oda.env`.
- Feil fra Oda-søk: prøv igjen senere. Oppskrifter caches i 14 dager.
