# Prompt text: one line per paragraph or bullet, with Dutch en and em dashes.
# ruff: noqa: E501, RUF001
"""Dutch briefing prompts: a morning stand-up and a week overview.

The texts are also the ``prompts`` templates in ``manifest.json``: Claude
Desktop only accepts a ``prompts/get`` answer that matches the template, so
``tests/test_briefing_prompts.py`` keeps the two identical.
"""

from __future__ import annotations

_COMMON = (
    "Alleen lezen: stuur, beantwoord of verwijder niets in Smartschool. "
    "Log nooit zelf in op Smartschool en open geen Smartschool-link in een "
    "browser; werkt iets niet, meld dat dan kort. Stel geen vragen; maak "
    "redelijke keuzes. Verzin niets; markeer afgeleide info met (vermoedelijk). "
    "Neem nooit wachtwoorden uit berichten over."
)

OCHTENDBRIEFING = f"""Je maakt een korte Nederlandse ochtendbriefing (daily standup) voor de leerling van deze Smartschool-login, met de Smartschool-tools. {_COMMON}

Data ophalen:
1. get_schedule(date_offset=0) — vandaag.
2. get_planned_elements(days_ahead=7, include_details=True) — hoofdbron voor toetsen (is_exam), taken, vrije dagen en activiteiten, met de leerstof (description) en de bijlagen van elk element.
3. Bijlagen van toetsen binnen 3 dagen: download ze met download_planned_attachment en vat ze samen in 1 of 2 zinnen. Bij andere bijlagen noem je enkel de bestandsnaam.
4. get_future_tasks als aanvulling (mag leeg zijn).
5. get_messages(include_body=True) met een bescheiden limit — focus op de laatste ~3 dagen of ongelezen.
6. get_results(limit=8, include_details=False): een resultaat met published_date van vandaag of gisteren is nieuw. Neem de feedback van de leerkracht kort mee. Bij een fout één zin in Varia.
7. get_reports: geeft alleen titel en datum, nooit de inhoud. Een nieuw rapport meld je in Varia, zonder inhoud.
8. get_homepage_blocks alleen op maandag.

Geen schooldag en niets urgents? Maak dan geen briefing.

Opmaak (Nederlands, geen nummering, max. ongeveer één telefoonscherm):
- Bovenaan: datum + de allerbelangrijkste zaak van vanochtend.
- Vandaag: meenemen, staking/vrije dag, toets vandaag, acties die vandaag geld of materiaal vragen.
- Vanavond te doen: studeren/taken met deadline morgen of overmorgen; toetsen als **Toets [vak] – [naam/onderwerp]**, met de leerstof letterlijk uit description. Pas als description én bijlagen leeg zijn: nagaan waar de leerstof staat.
- Rest van de week: stakingen/vrije dagen in vet, toetsen met datum en de leerstof in een halve zin, meenemen, oudercontact.
- Varia: mededelingen, betalingen, strooibriefjes; nieuwe punten (met korte feedback) en een nieuw rapport.

Een onderdeel zonder inhoud laat je volledig weg: geen kop en geen regel als "geen taken" of "niets gepland".

Geen lesuren of lokalen tenzij nodig. Lever alleen in de chat."""

WEEKBRIEFING = f"""Je maakt een korte Nederlandse weekbriefing voor de leerling van deze Smartschool-login, met de Smartschool-tools. {_COMMON}

Data ophalen:
1. get_planned_elements(days_ahead=7, include_details=True) — hoofdbron, met de leerstof (description) en de bijlagen van elk element. Download bijlagen niet; noem enkel de bestandsnaam.
2. get_schedule(date_offset=0) en get_schedule(date_offset=1) als context.
3. get_future_tasks als aanvulling.
4. get_messages(include_body=True) — berichten die deze week actie vragen.
5. get_results(limit=8, include_details=False): resultaten met een published_date van de voorbije 7 dagen, met de feedback van de leerkracht kort erbij.
6. get_reports: geeft alleen titel en datum, nooit de inhoud. Meld enkel een nieuw rapport.
7. get_homepage_blocks als relevant.

Doel: overzicht van de komende schoolweek, geen ochtend-standup.

Opmaak (Nederlands, geen nummering):
- Bovenaan: weekbereik + één zin met het zwaartepunt.
- Deze week: toetsen en deadlines per dag (vak + naam/onderwerp + datum + leerstof letterlijk uit description), chronologisch. Pas als description én bijlagen leeg zijn: nagaan waar de leerstof staat.
- Meebrengen & acties: kledij, materiaal, geld, strooibriefjes, inschrijvingen, oudercontact — met de dag.
- Aandacht / berichten: schoolberichten die deze week nog spelen.
- Punten: alleen nieuwe of opvallende resultaten, met korte feedback, en een nieuw rapport zonder inhoud.

Een onderdeel zonder inhoud laat je volledig weg: geen kop en geen regel als "geen taken" of "niets gepland".

Vet voor stakingen, vrije dagen en toetsen. Geen complete lesurenlijst. Lever alleen in de chat."""

BRIEFINGS: dict[str, str] = {
    "ochtendbriefing": OCHTENDBRIEFING,
    "weekbriefing": WEEKBRIEFING,
}

DESCRIPTIONS: dict[str, str] = {
    "ochtendbriefing": "Korte Nederlandse ochtendbriefing: vandaag, vanavond en de rest van de week",
    "weekbriefing": "Korte Nederlandse weekbriefing voor de komende schoolweek",
}
