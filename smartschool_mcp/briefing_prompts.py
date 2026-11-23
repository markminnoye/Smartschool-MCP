"""Dutch parent briefing prompts for Claude Desktop / MCP clients.

These are instruction templates. The model must call Smartschool tools to
fetch data; the prompts themselves do not contact the portal.
"""

from __future__ import annotations


def _child_clause(child: str) -> str:
    name = child.strip()
    if name:
        return (
            f"Kind: {name}. Roep get_children aan en switch_child naar dit kind "
            "als dat nog niet het actieve kind is. Blijf op dit kind."
        )
    return (
        "Kind: neem de naam uit Configure / het opgeslagen profiel. "
        "Roep get_children aan en switch_child naar dat kind als nodig. "
        "Blijf op dat kind."
    )


def _join(*parts: str) -> str:
    return "\n".join(parts)


def ochtendbriefing_text(child: str = "") -> str:
    """Compact daily standup: what matters this morning and tonight."""
    return _join(
        "Je maakt een korte Nederlandse ochtendbriefing (daily standup) voor "
        "één kind via de Smartschool-tools. Alleen lezen: stuur, beantwoord of "
        "verwijder niets in Smartschool. Stel geen vragen; maak redelijke "
        "keuzes. Verzin niets; markeer afgeleide info met (vermoedelijk). "
        "Neem nooit wachtwoorden uit berichten over.",
        "",
        _child_clause(child),
        "",
        "Data ophalen:",
        "1. get_schedule(date_offset=0) — vandaag.",
        "2. get_planned_elements van vandaag t/m +7 dagen "
        "(includes courses,locations als ondersteund) — "
        "hoofdbron voor taken en toetsen.",
        "3. get_future_tasks als aanvulling (mag leeg zijn).",
        "4. get_messages (inbox, bescheiden limiet, bodies aan) — "
        "focus op ~3 dagen of ongelezen.",
        "5. get_results (kleine limiet) — alleen als er iets nieuws is; "
        "bij fout één zin in Varia.",
        "6. get_homepage_blocks alleen op maandag.",
        "",
        "Geen schooldag (vakantie, vrije dag, leeg rooster) en niets "
        "urgents? Maak dan geen briefing.",
        "",
        "Opmaak (Nederlands, geen nummering, lege onderdelen weglaten, "
        "max. ongeveer één telefoonscherm):",
        "- Bovenaan: datum + de allerbelangrijkste zaak van vanochtend.",
        "- Vandaag: meenemen, staking/vrije dag, toets vandaag, acties die "
        "vandaag geld of materiaal vragen.",
        "- Vanavond zeker: studeren/taken met deadline morgen of overmorgen; "
        "toetsen als **Toets [vak] - [naam/onderwerp]**.",
        "- Rest van de week: stakingen/vrije dagen in vet, toetsen met datum, "
        "meenemen, oudercontact.",
        "- Varia: mededelingen, betalingen, strooibriefjes; nieuwe punten "
        'alleen als nieuw. Negeer ruis zoals "Smartschool App geactiveerd".',
        "",
        "Geen lesuren of lokalen tenzij nodig. Vet alleen voor wat niet "
        "vergeten mag. Lever alleen in de chat (geen mail tenzij de gebruiker "
        "dat apart vraagt).",
        "",
    )


def weekbriefing_text(child: str = "") -> str:
    """Weekly overview: the coming school week, not this-morning urgency."""
    return _join(
        "Je maakt een korte Nederlandse weekbriefing voor één kind via de "
        "Smartschool-tools. Alleen lezen: stuur, beantwoord of verwijder "
        "niets in Smartschool. Stel geen vragen; maak redelijke keuzes. "
        "Verzin niets; markeer afgeleide info met (vermoedelijk). Neem nooit "
        "wachtwoorden uit berichten over.",
        "",
        _child_clause(child),
        "",
        "Data ophalen:",
        "1. get_planned_elements van vandaag t/m +7 dagen "
        "(includes courses,locations als ondersteund) — hoofdbron.",
        '2. get_schedule voor vandaag en eventueel morgen als context voor "nu".',
        "3. get_future_tasks als aanvulling.",
        "4. get_messages (inbox, bescheiden limiet, bodies aan) — berichten "
        "die deze week actie vragen.",
        "5. get_results (kleine limiet) — recente nieuwe punten.",
        "6. get_homepage_blocks voor mededelingen of maandmenu als relevant.",
        "",
        "Doel: overzicht van de komende schoolweek, geen ochtend-standup. "
        "Wat moet er deze week gebeuren, voorbereid of meegenomen worden?",
        "",
        "Opmaak (Nederlands, geen nummering, lege onderdelen weglaten, "
        "kort genoeg voor één scherm of twee):",
        "- Bovenaan: weekbereik (datums) + één zin met het zwaartepunt van de week.",
        "- Deze week: toetsen en deadlines per dag "
        "(vak + naam/onderwerp + datum), in chronologische volgorde.",
        "- Meebrengen & acties: kledij, materiaal, geld, strooibriefjes, "
        "inschrijvingen, oudercontact — met de dag waarop het speelt.",
        "- Aandacht / berichten: schoolberichten die deze week nog spelen.",
        "- Punten: alleen nieuwe of opvallende resultaten.",
        "",
        "Vet voor stakingen, vrije dagen en toetsen. Geen complete "
        "lesurenlijst. Lever alleen in de chat (geen mail tenzij de gebruiker "
        "dat apart vraagt).",
        "",
    )
