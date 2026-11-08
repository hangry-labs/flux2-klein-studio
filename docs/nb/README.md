<p align="center"><a href="../README.md">English</a> · <strong>Norsk bokmål</strong> · <a href="../pl/README.md">Polski</a> · <a href="../ja/README.md">日本語</a> · <a href="../zh/README.md">简体中文</a> · <a href="../es/README.md">Español</a></p>

# FLUX.2 Klein Studio · Dokumentasjon

Denne innføringen dekker de vanlige arbeidsflytene på norsk bokmål. Detaljerte API-skjemaer, målerapporter, adapteroppskrifter, utgivelsesregler og valideringshistorikk vedlikeholdes på engelsk. Lenker nedenfor er merket tilsvarende.

[Prosjektoversikt](../../README.nb.md) · [Docker Hub](../dockerhub.nb.md).

## Kort brukerveiledning

**Generer:** beskriv motiv, omgivelser og lys. Bruk fire trinn som utgangspunkt. Bredde og høyde må være 64–1920 og delelige med 16. Eksempelknappen fyller bare beskrivelsen. Flere trinn er ikke automatisk bedre.

**Referanser:** last opp én til fire bilder, eller velg fra galleriet. Hver fil kan være høyst 16 MiB. Bildene orienteres, gjennomsiktighet legges på hvitt, og hver referanse begrenses til omtrent én megapiksel med mål delelige med 16. Referanserekkefølgen er viktig. Grunnmodellen bruker flere referanser; redigeringsadaptere bruker én.

**Grønn utfylling:** mal med #00FF00 eller øk lerretet for grønne kanter. Den faste utfyllingsinstruksen er alltid med; ønsket innhold er valgfritt. Små merker kan bli igjen. **Rød zoom:** rektangelet følger resultatets sideforhold. Dra inni for å flytte; velg utenfor for å erstatte. Bruk resultatet for et nytt nærbilde.

**Galleri:** velg lagring før generering. Et lagret redigert bilde beholder alle klargjorte referanser og oppskriften, men ikke det opprinnelige råbildet eller penselhistorikken. Gjenåpning bruker fast frø. PNG og WebP bevarer pikslene; JPEG er tapsbasert. Bekreftet sletting fjerner hele jobbens lokale filer.

**System:** standard ledighetstid er 300 sekunder. Null laster ut etter generering. Frigi ubrukt VRAM beholder vektene, mens Last ut nå fjerner dem. Størrelsesvalg påvirker nye HTTP/MCP-forespørsler og verktøyannonsering, ikke lokale lerretskontroller. GPU-grafene måler bare når System vises.

## Filer og minne

Volumet `flux2_klein_studio_data` monteres på `/app/persistent` og beholder modeller, hurtigbuffer, innstillinger og lagrede resultater. Gjenbruk det når containeren byttes. Uten avkrysning for gallerilagring er resultatet midlertidig; det forsvinner ved omstart eller når grensen på 100 forhåndsvisninger nås. Lagrede resultater fjernes bare når du sletter dem. Sletting av datavolumet kan slette både hurtigbuffer og lagrede resultater.

Alle modellkomponenter ligger på GPU-en, uten avlasting til CPU eller disk. Vekter beholdes som standard i fem minutter etter siste jobb. Ubrukt arbeidsminne frigis automatisk; System tilbyr også umiddelbar frigjøring. Størrelsestabellen viser målte referanseverdier for generering uten bilder, ikke et løfte om at alle redigeringer får plass.

## Apper og agenter

Images-API-et bruker `/v1/images/generations` og `/v1/images/edits`, med modellnavnet `flux2-klein-4b`. MCP bruker Streamable HTTP på `/mcp/`; agenter bør lese verktøyet `skill` først. Navngitte størrelser som `square_big` og adapter-ID-er beholdes uendret. API-et støtter `n=1..16`; forespørsler venter i en felles FIFO-kø, med ett bilde om gangen på GPU-en. Eksemplet under bruker Bash-sitering.

```bash
curl http://localhost:7860/v1/images/generations -H "Content-Type: application/json" -d '{"model":"flux2-klein-4b","prompt":"A glass robot tending orchids in a sunlit greenhouse","size":"square_big","seed":12345}'
```

## Detaljert engelsk dokumentasjon

- [Nettleserstudio (engelsk)](../studio.md)
- [HTTP og MCP (engelsk)](../agent-api.md)
- [Adaptere og faste instrukser (engelsk)](../lora.md)
- [Målemetode og resultater (engelsk)](../benchmarks.md)
- [Modellfunksjoner og begrensninger (engelsk)](../model-capabilities.md)
- [Test- og valideringshistorikk (engelsk)](../validation.md)
- [Containerutgivelser (engelsk)](../RELEASING.md)
- [Målt VRAM med flere referanser (engelsk)](../../benchmarks/multiref/README.md)

## Personvern og vilkår

Generering skjer lokalt. Bare eksplisitt valgt gallerilagring beholder resultater og beskrivelser; første nedlasting trenger nettverk. Hold porten på localhost eller bruk et egnet autentisert mellomlag. `STUDIO_API_KEY` beskytter `/v1` og `/mcp`, ikke det lokale nettleser-API-et. Bruk referansebilder med nødvendige tillatelser og ikke framstill genererte bilder som ekte dokumentasjon.

Programkoden er Apache-2.0. Modeller, adaptere og avhengigheter beholder egne vilkår. Se LICENSE, NOTICE og THIRD_PARTY.md; programlisensen erstatter ikke komponentvilkårene.

[LICENSE](../../LICENSE) · [NOTICE](../../NOTICE) · [THIRD_PARTY.md](../../THIRD_PARTY.md).
