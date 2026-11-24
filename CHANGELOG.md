# Wijzigingslog

Alle belangrijke wijzigingen aan dit project staan in dit bestand.

De opmaak volgt [Keep a Changelog](https://keepachangelog.com/nl/1.1.0/)
en de versienummers volgen [Semantic Versioning](https://semver.org/lang/nl/).

## [Niet uitgebracht]

### Gewijzigd

- De `smartschool`-bibliotheek komt van PyPI `0.13.0` (tag `bd98fd07`), niet langer van de fork `markminnoye/smartschool@517de70`. Het `.mcpb` vendor die wheel-bron. `get_schedule` en `get_planned_elements` blijven de ruwe Planner-calendar-GET gebruiken (inclusief optionele `includes`), omdat 0.13.0 `PlannedElements` standaard alleen opdrachten en to-dos haalt en `description` / `uploadFolders` weggooit.
- `get_future_tasks` geeft de planner-fallback `element_types=planned-assignments` (0.13.0 heeft geen `types=`).
- Vrije-tekstlocaties (`type=text`) tonen het veld `text`; een lokaal blijft `title`.

### Verwijderd

- De runtime-patch `graphic_color.py` voor onbekende cijferkleuren. 0.13.0 accepteert `blue` en maakt van andere namen ad-hoc-leden. `download_attachment` blijft `session.get` gebruiken zodat een loginpagina of een foute HTTP-status geweigerd wordt.

## [1.0.0] - 2026-11-23

Eerste publieke versie van **Smartschool voor ouders**, de extensie voor Claude Desktop. De eerdere 0.x-testversies zijn ingetrokken. De extensie bouwt verder op de oorspronkelijke Smartschool MCP van Mauro Druwel en op de portaalbibliotheek [`svaningelgem/smartschool`](https://github.com/svaningelgem/smartschool), allebei met een MIT-licentie.

### Verspreiding

- Eén `.mcpb`-bestand per GitHub-release (`smartschool-mcp-<versie>.mcpb`), gebouwd door `mcpb.yml`. Er wordt niets op PyPI of in het MCP-register gepubliceerd. Het bestand bevat de vastgezette fork `markminnoye/smartschool`.

### Wat de extensie kan (alleen lezen)

- Lesrooster en planning uit Planner: `get_schedule`, `get_planned_elements`, `get_future_tasks`, met de bijlagen bij planneritems en het downloaden ervan.
- Resultaten met de feedback van de leerkracht en, als de school ze deelt, het klasgemiddelde en de mediaan: `get_results`.
- Berichten van de school, met zoeken en bijlagen: `get_messages`, `get_attachments`, `download_attachment`.
- Documenten uit de vakken, periodes, rapporten, hulplinks en de blokken "in de kijker" op de startpagina.
- Meerdere kinderen op één ouderaccount: `get_children` en `switch_child`, ook voor een kind op een andere school.
- De Nederlandse prompts **ochtendbriefing** (overzicht van de dag) en **weekbriefing** (de komende schoolweek) in Claude Desktop.
- Er wordt niets verstuurd, beantwoord of gewijzigd in Smartschool.

### Instellen en privacy

- Het venster Configureren in Claude Desktop vraagt één keer naar school, gebruikersnaam, wachtwoord, de geboortedatum en de naam van je kind. Die gegevens komen in `~/.config/smartschool/credentials.json`; op een Mac gaan het wachtwoord en de geboortedatum naar de Sleutelhanger.
- Eén poging met het wachtwoord: een geweigerde login schrijft `auth_failed` en wordt nooit opnieuw geprobeerd. Een netwerkfout blokkeert niets.
- Downloads komen alleen in `~/Downloads/smartschool` (of `SMARTSCHOOL_DOWNLOAD_DIR`) en de submappen daarvan, overschrijven nooit een bestaand bestand en weigeren een loginpagina van Smartschool.

### Ook inbegrepen

- Een Claude Code-plugin in `claude-plugin/` met de Nederlandse skill `daily-briefing` en scripts die alleen lezen.
- Optioneel transport via Streamable HTTP: luistert standaard op `127.0.0.1` en vraagt `MCP_API_KEY` op elk ander adres. De universele OAuth-modus blokkeert een gebruikersnaam na een geweigerde login 15 minuten, in het geheugen.

[Niet uitgebracht]: https://github.com/markminnoye/Smartschool-MCP/compare/v1.0.0...HEAD
[1.0.0]: https://github.com/markminnoye/Smartschool-MCP/releases/tag/v1.0.0
