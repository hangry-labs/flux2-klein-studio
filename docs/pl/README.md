<p align="center"><a href="../README.md">English</a> · <a href="../nb/README.md">Norsk bokmål</a> · <strong>Polski</strong> · <a href="../ja/README.md">日本語</a> · <a href="../zh/README.md">简体中文</a> · <a href="../es/README.md">Español</a></p>

# FLUX.2 Klein Studio · Dokumentacja

To polskie wprowadzenie opisuje codzienne zadania. Szczegółowe schematy API, raporty pomiarów, ustawienia adapterów, zasady wydań i historia walidacji są utrzymywane po angielsku. Poniższe linki są odpowiednio oznaczone.

[Przegląd projektu](../../README.pl.md) · [Docker Hub](../dockerhub.pl.md).

## Krótki przewodnik

**Generowanie:** opisz temat, otoczenie i światło. Zacznij od czterech kroków. Szerokość i wysokość muszą wynosić 64–1920 i być podzielne przez 16. Przycisk przykładu wypełnia tylko opis. Więcej kroków nie oznacza automatycznie lepszego wyniku.

**Referencje:** prześlij od jednego do czterech obrazów lub wybierz wynik z galerii. Każdy plik może mieć najwyżej 16 MiB. Obrazy są orientowane, przezroczystość nakładana na biel, a każda referencja ograniczana do około jednego megapiksela z wymiarami podzielnymi przez 16. Kolejność ma znaczenie. Model bazowy obsługuje wiele referencji; adaptery edycji jedną.

**Zielone wypełnienie:** maluj #00FF00 lub powiększ płótno, aby dodać zielone brzegi. Stała instrukcja jest zawsze dodawana; opis zawartości jest opcjonalny. Drobne znaki mogą pozostać. **Czerwone przybliżenie:** prostokąt zachowuje proporcje wyniku. Przeciągaj wewnątrz, aby przesunąć; zaznacz na zewnątrz, aby zastąpić. Użyj wyniku do kolejnego zbliżenia.

**Galeria:** wybierz zapis przed generowaniem. Zapisana edycja zachowuje wszystkie przygotowane referencje i ustawienia, ale nie surowy plik źródłowy ani historię pędzla. Otwarcie ustawień przywraca stałe ziarno. PNG i WebP zachowują piksele; JPEG jest stratny. Potwierdzone usunięcie kasuje wszystkie lokalne pliki zadania.

**System:** domyślna bezczynność to 300 sekund. Zero zwalnia model po generowaniu. Zwolnienie nieużywanej VRAM zachowuje wagi, a Zwolnij teraz je usuwa. Wybór rozmiarów wpływa na nowe żądania HTTP/MCP i ogłaszane narzędzia, nie lokalne wymiary płótna. Wykresy GPU próbkują tylko wtedy, gdy System jest widoczny.

## Pliki i pamięć

Wolumin `flux2_klein_studio_data` montowany w `/app/persistent` zachowuje modele, pamięć podręczną, ustawienia i zapisane wyniki. Używaj go ponownie po wymianie kontenera. Bez zaznaczenia zapisu wynik jest tymczasowy; znika po restarcie lub po wyparciu z puli 100 podglądów. Zapisane wyniki są usuwane tylko na twoje polecenie. Usunięcie woluminu może skasować zarówno cache, jak i zapisane wyniki.

Wszystkie komponenty modelu działają na GPU, bez przenoszenia na CPU lub dysk. Wagi domyślnie pozostają w pamięci przez pięć minut po ostatnim zadaniu. Nieużywana pamięć robocza jest zwalniana automatycznie; System umożliwia też natychmiastowe zwolnienie. Tabela rozmiarów pokazuje pomiary generowania z samego tekstu, a nie gwarancję, że każda edycja zmieści się w pamięci.

## Aplikacje i agenty

API obrazów udostępnia `/v1/images/generations` i `/v1/images/edits` z modelem `flux2-klein-4b`. MCP używa Streamable HTTP pod `/mcp/`; agenty powinny najpierw odczytać narzędzie `skill`. Nazwy rozmiarów, np. `square_big`, i identyfikatory adapterów pozostają bez zmian. API obsługuje `n=1..16`; żądania czekają we wspólnej kolejce FIFO, a GPU generuje jeden obraz naraz. Poniższy przykład używa składni cytowania Bash.

```bash
curl http://localhost:7860/v1/images/generations -H "Content-Type: application/json" -d '{"model":"flux2-klein-4b","prompt":"A glass robot tending orchids in a sunlit greenhouse","size":"square_big","seed":12345}'
```

## Szczegółowa dokumentacja angielska

- [Studio w przeglądarce (angielski)](../studio.md)
- [HTTP i MCP (angielski)](../agent-api.md)
- [Adaptery i stałe instrukcje (angielski)](../lora.md)
- [Metodologia i wyniki pomiarów (angielski)](../benchmarks.md)
- [Możliwości i ograniczenia modelu (angielski)](../model-capabilities.md)
- [Historia testów i walidacji (angielski)](../validation.md)
- [Wydawanie kontenerów (angielski)](../RELEASING.md)
- [VRAM przy wielu referencjach (angielski)](../../benchmarks/multiref/README.md)

## Prywatność i warunki

Generowanie działa lokalnie. Wyniki i opisy pozostają zapisane tylko po wybraniu zapisu do galerii; pierwsze pobranie wymaga sieci. Zachowaj port na localhost lub użyj odpowiednio zabezpieczonego proxy. `STUDIO_API_KEY` chroni `/v1` i `/mcp`, nie lokalne API przeglądarki. Używaj referencji z wymaganymi zgodami i nie przedstawiaj generowanych obrazów jako autentycznej dokumentacji.

Kod aplikacji ma licencję Apache-2.0. Modele, adaptery i zależności zachowują własne warunki. Zobacz LICENSE, NOTICE i THIRD_PARTY.md; licencja aplikacji nie zastępuje warunków komponentów.

[LICENSE](../../LICENSE) · [NOTICE](../../NOTICE) · [THIRD_PARTY.md](../../THIRD_PARTY.md).
