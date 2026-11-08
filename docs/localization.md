# Languages and translations

[English](README.md) · [Norsk bokmål](nb/README.md) · [Polski](pl/README.md) · [日本語](ja/README.md) · [简体中文](zh/README.md) · [Español](es/README.md)

## Reader-facing support

The UI supports English (`en`), Norwegian Bokmål (`nb`), Polish (`pl`), Japanese
(`ja`), Simplified Chinese (`zh`) and Spanish (`es`), matching Faster Qwen Studio.
English is the default. Choose a language in the header, or open `/en`, `/nb`,
`/pl`, `/ja`, `/zh` or `/es`, with or without a trailing slash.

An explicit language URL takes precedence over the remembered browser preference.
The root URL uses that preference when available and otherwise uses English.
The preference lives in browser local storage; blocked storage does not prevent
language switching. No prompts or pictures are persisted with that preference.
Switching language updates the current view without navigation or reloading, so
typed prompts, references, paint strokes, zoom selection, settings and jobs remain.

Language choice changes interface messages, placeholders, accessibility text and
known diagnostic wrappers. Unknown service diagnostics remain verbatim. It does
not translate user prompts, recipes, API fields/IDs or the fixed English adapter
instructions. It does not qualify multilingual image-generation quality.

Translated READMEs are `README.<locale>.md`. Docker Hub language links lead to
`docs/dockerhub.<locale>.md`; each page uses absolute image and documentation URLs
and fits the publication size limit. `docs/<locale>/README.md` provides a translated
practical introduction. Detailed technical guides, benchmark data, release history
and legal notices remain in English and are labeled accordingly. English remains
the canonical source for release identity, avoiding stale translated version pins.

## Maintaining the UI catalogs

The bundled `static/locales/*.json` files use English source messages as keys.
`data-i18n` marks static text; `data-i18n-title`, `data-i18n-aria-label`,
`data-i18n-placeholder` and `data-i18n-alt` mark translated attributes. Mark only
text leaves; do not put a translation marker on a parent containing controls.

JavaScript uses `msg('A fixed message')` or a tagged template such as
``msg`Waiting in queue · Position ${position}` ``. Catalog entries use positional
variables (`{0}`, `{1}`, …). Every locale must contain the same keys and variables
as English. Missing or empty translated values fall back to English at runtime.
Use text content and attributes, never translated HTML. Keep user descriptions
outside translation calls and keep machine values separate from displayed labels.

Dynamic components handle `ui-locale-changed` to refresh labels from their current
state. Preserve select values, checkbox choices and in-progress editor content.
Update catalogs and the relevant documentation together when adding controls.
Some shared phrases follow Faster Qwen's MIT-licensed catalogs; attribution is
retained in NOTICE and THIRD_PARTY.md.

## Checks

```shell
task validate
task test:ui
task docker:test-build
```

The JavaScript tests use Node.js 22 or newer and its built-in runner, without npm
packages or a frontend build. Node is a development check dependency; the runtime
container serves plain JavaScript and does not need Node. Python tests verify
catalog parity, interpolation, HTML markers, direct routes and translated guides.
JavaScript tests cover URL/preference precedence, fallback, unavailable storage,
catalog failures, racing locale requests and preservation of select values.

For browser validation, exercise all six languages on Generate, Refine, Gallery
and System at desktop and mobile widths, including the compact header. Change
language during a generation and while painting/selecting a zoom area. Verify
output recipes and protocol values are unchanged, no runtime assets require a
CDN, and failures leave the previous language usable. A locale change is not a
reason to rerun performance benchmarks.
