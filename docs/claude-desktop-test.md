# Smartschool uitproberen in Claude Desktop

Dit is een test voor een ouder. Je downloadt één bestand, opent het in Claude Desktop, en vult daarna school, gebruikersnaam, wachtwoord, geboortedatum en de naam van je kind in. Dat doe je in het venster **Configure**. Je maakt zelf geen accountbestand aan.

Deze testversie is **0.3.0-rc.1**. Elke nieuwe testbuild krijgt een hoger nummer, zodat Claude **Update** toont en de builds uit elkaar te houden zijn. De volgende is **0.3.0-rc.2**, daarna **0.3.0-rc.3**, enzovoort. Die naam staat bij de extensie.

## 1. Het bestand downloaden

Je zoekt een bestand dat eindigt op `.mcpb`. GitHub stopt dat bestand in een zip.

1. Open de pagina van deze wijziging op GitHub (de link die je kreeg).
2. Klik bovenaan op **Checks**.
3. Klik op de regel **MCPB**.
4. Onderaan de pagina, bij **Artifacts**, download je **smartschool-mcp-mcpb**.
5. Dubbelklik de zip. Daarin zit `smartschool-mcp.mcpb`.

Bewaar dat `.mcpb`-bestand ergens waar je het terugvindt, bijvoorbeeld op het bureaublad.

## 2. Installeren in Claude Desktop

1. Dubbelklik `smartschool-mcp.mcpb`.
2. Claude Desktop opent de extensie.
3. Gebeurt dat niet: open Claude Desktop, ga naar **Instellingen**, dan **Extensies**, en kies daar het `.mcpb`-bestand.

De eerste keer heeft Claude internet nodig om de rest van het programma op te halen. Dat kan een minuut duren. Blijf in Claude. Er komt geen zwart venster.

## 3. Account invullen in Configure

1. Open Claude Desktop.
2. Ga naar **Instellingen**, dan **Extensies**, dan **Smartschool**, dan **Configure**.
3. Vul deze velden in:
   - **School**: het subdomein, bijvoorbeeld `dering` of `depass`
   - **Gebruikersnaam**
   - **Wachtwoord**
   - **Geboortedatum van het kind**, als `jjjj-mm-dd` (bijvoorbeeld `2014-03-21`)
   - **Naam van het kind**
4. Sluit Claude helemaal af. Op een Mac: menu **Claude**, **Stop Claude**. Op Windows: klik met de rechtermuisknop op het Claude-icoon naast de klok en kies afsluiten.
5. Open Claude opnieuw.

De extensie bewaart dat account zelf één keer, ook voor de andere Smartschool-hulp op deze computer (`credentials.json`; op een Mac gaat het wachtwoord naar de Sleutelhanger). Je opent of bewerkt dat bestand niet. Staat dit account er al, dan gebruikt Claude dat account en maakt Configure geen tweede kopie.

Staan er meerdere accounts op de computer, dan zegt Claude **Meerdere profielen**. Vul in Configure de school en de gebruikersnaam van het account dat je wilt, sluit Claude helemaal af, en open het opnieuw.

## 4. Vragen om te stellen

Wacht tot de extensie aan staat. Stel de vragen één voor één, in gewoon Nederlands. Gebruik de naam die je bij Configure invulde.

Een geslaagde test toont gegevens uit Smartschool. Een leeg antwoord kan kloppen (geen taken, geen nieuwe berichten). Een zin met `error` of `LOGIN FAILED` betekent dat de test stopte.

| Vraag | Verwacht resultaat |
| --- | --- |
| Welke kinderen hangen aan dit account? (`get_children`) | De namen van de gekoppelde kinderen, en wie er nu actief is. |
| Zet het account op de naam die ik bij Configure invulde. (`switch_child`) | Claude wisselt naar dat kind. Daarna gaan rooster en punten over dat kind. |
| Welke vakken heeft dit kind? (`get_courses`) | Een lijst met vakken en leerkrachten. |
| Wat zijn de laatste punten? (`get_results`) | Cijfers, met vak en datum. |
| Welke taken moet dit kind nog maken? (`get_future_tasks`) | Taken, gegroepeerd per dag. |
| Zijn er nieuwe berichten? (`get_messages`) | Berichten met afzender en onderwerp. |
| Wat is het rooster van vandaag? (`get_schedule`) | De lessen van vandaag, met begin- en einduur. |
| Wat staat er de komende weken in de planner? (`get_planned_elements`) | Planneritems over meerdere dagen. |
| Welke periodes zijn er dit schooljaar? (`get_periods`) | De periodes of trimesters, met datums. |
| Welke rapporten staan er? (`get_reports`) | De rapporten die de school heeft klaargezet. |
| Welke links voor leerlingenbegeleiding zijn er? (`get_student_support_links`) | De zichtbare links van de school. |
| Wat staat er in de kijker op de startpagina? (`get_homepage_blocks`) | Blokken zoals een maandmenu of een aankondiging, vaak met een foto. |

Pas daarna, als een bericht of een blok een bijlage of foto heeft:

| Vraag | Verwacht resultaat |
| --- | --- |
| Welke bijlagen heeft dat bericht? (`get_attachments`) | De namen van de bestanden bij dat ene bericht. |
| Sla die bijlage op. (`download_attachment`) | Het bestand staat in de map Downloads, in een map `smartschool`. |
| Sla die foto van de startpagina op. (`download_homepage_image`) | De foto staat in diezelfde map Downloads. |
| Welke bestanden hangen aan die taak of les? (`get_planner_attachments`) | De bijlagen van dat planneritem, als de school ze meestuurt. |
| Sla dat plannerbestand op. (`download_planner_file`) | Het bestand staat in de map Downloads, in een map `smartschool`. |
| Welke documenten heeft dit vak? (`get_course_documents`) | De documentenlijst van dat vak. |
| Sla dat vakdocument op. (`download_course_document`) | Het bestand staat in de map Downloads, in een map `smartschool`. |

## 5. Als inloggen mislukt

Stop. Stel de vraag niet opnieuw.

Een fout wachtwoord kan het Smartschool-account van de school blokkeren. Deze extensie stuurt het wachtwoord daarom één keer. Lukt dat niet, dan schrijft ze een bestand met de naam `auth_failed` en stuurt ze het wachtwoord niet nog een keer. Ook een antwoord dat op de inlogpagina blijft, inclusief een adres met `error=1`, telt als die ene mislukte poging.

Je herkent de stop aan de zin **LOGIN FAILED, niet opnieuw proberen**, met het pad naar dat bestand.

Zo ga je verder:

1. Open opnieuw **Instellingen > Extensies > Smartschool > Configure** en verbeter het wachtwoord of de geboortedatum. Je bewerkt geen bestand.
2. Gooi het bestand `auth_failed` weg. Het staat in een map op je computer: eerst de school (bijvoorbeeld `dering`), daarna de Smartschool-gebruikersnaam. Dat is niet de voornaam van je kind.

Op een Mac:

1. Open Finder.
2. Klik in de menubalk op **Ga**, dan **Ga naar map**.
3. Plak `~/.cache/smartschool` en druk op Return.
4. Open de map met de school, en daarna de map met de gebruikersnaam.
5. Sleep `auth_failed` naar de prullenbak.

Op Windows:

1. Open Verkenner.
2. Klik op de adresbalk.
3. Plak `%USERPROFILE%\.cache\smartschool` en druk op Enter.
4. Open de map met de school, en daarna de map met de gebruikersnaam.
5. Verwijder `auth_failed`.

3. Sluit Claude Desktop helemaal. Op een Mac: menu **Claude**, **Stop Claude**. Op Windows: klik met de rechtermuisknop op het Claude-icoon naast de klok en kies afsluiten. Open Claude daarna opnieuw.
4. Stel één testvraag.

Heb je het wachtwoord al een paar keer op de Smartschool-website geprobeerd, dan kan de school het account zelf geblokkeerd hebben. Het bestand wissen maakt dat niet ongedaan. Neem contact op met de school en probeer het wachtwoord niet verder.

## 6. Extra: bestandsnaam, plannerbijlage en klasgemiddelde

| Wat je vraagt | Wat je verwacht |
| --- | --- |
| Sla dit vakdocument op. (`download_course_document`) | Het bestand in Downloads, map `smartschool`, heeft een extensie zoals `.pdf` of `.docx`. Ook als Smartschool de naam zonder extensie toont. |
| Welke bestanden hangen aan die taak? (`get_planner_attachments`) Daarna: sla dat plannerbestand op. (`download_planner_file`) | Lukt het, dan staat het bestand in die map en zegt Claude welk pad werkte. Lukt het niet, dan komt een Nederlandse fout. Er komt geen leeg bestand. |
| Toon de bijlagen van dat planneritem met include_raw op true. (`get_planner_attachments`) | Veldnamen van de bijlage, en links die de school meestuurt. Geen cookies en geen wachtwoord. Gebruik dit als opslaan niet lukt, en stuur die veldnamen door. |
| Wat zijn de laatste punten, met het klasgemiddelde en de mediaan? (`get_results`) | Gemiddelde en mediaan staan erbij als de school ze toont. Anders blijven ze leeg. Een school of leerkracht mag ze verbergen. |
| Toon van het eerste cijfer de detailvelden, met include_raw op true. (`get_results`) | Je ziet of Smartschool die getallen meestuurt, aan de veldnamen. Geen cookies en geen namen. |
