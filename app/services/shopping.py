"""Shopping list helpers: a built-in catalog that gives items an emoji and a store aisle.

Classification is derived on every render from the item name, so nothing is stored and the
catalog can grow without a migration. Emoji instead of fetched photos: no third party learns
what someone buys, and they render offline.
"""

# Aisles in the order you'd usually walk through a German supermarket.
CATEGORIES = (
    ("produce", "Obst & Gemüse", "🥦"),
    ("bakery", "Brot & Backwaren", "🥖"),
    ("dairy", "Milch, Käse & Eier", "🧀"),
    ("meat", "Fleisch & Fisch", "🥩"),
    ("pantry", "Vorrat", "🥫"),
    ("frozen", "Tiefkühl", "🧊"),
    ("sweets", "Süßes & Snacks", "🍫"),
    ("drinks", "Getränke", "🥤"),
    ("household", "Drogerie & Haushalt", "🧴"),
    ("other", "Sonstiges", "🛒"),
)
FALLBACK_CATEGORY = "other"

# category: (suggested name, emoji, space-separated stems matched anywhere in the item name).
# Stems are singular so a plural and its compound end at the same spot ("bohne"/"kaffeebohne");
# "_" stands for a space inside a stem ("red_bull").
CATALOG = {
    "produce": (
        ("Äpfel", "🍎", "apfel äpfel"),
        ("Birnen", "🍐", "birne"),
        ("Bananen", "🍌", "banane"),
        ("Orangen", "🍊", "orange apfelsine mandarine clementine grapefruit"),
        ("Zitronen", "🍋", "zitrone limette"),
        ("Weintrauben", "🍇", "traube"),
        ("Erdbeeren", "🍓", "erdbeere"),
        ("Heidelbeeren", "🫐", "beere"),
        ("Kirschen", "🍒", "kirsche"),
        ("Pfirsiche", "🍑", "pfirsich nektarine aprikose"),
        ("Pflaumen", "🫐", "pflaume zwetschge"),
        ("Feigen", "🍑", "feige dattel rosine"),
        ("Mango", "🥭", "mango"),
        ("Ananas", "🍍", "ananas"),
        ("Melone", "🍉", "melone"),
        ("Kiwis", "🥝", "kiwi"),
        ("Avocados", "🥑", "avocado"),
        ("Tomaten", "🍅", "tomate"),
        ("Gurke", "🥒", "gurke zucchini"),
        ("Paprika", "🫑", "paprika"),
        ("Chili", "🌶️", "chili peperoni"),
        ("Karotten", "🥕", "karotte möhre"),
        ("Kartoffeln", "🥔", "kartoffel"),
        ("Süßkartoffeln", "🍠", "süßkartoffel"),
        ("Zwiebeln", "🧅", "zwiebel lauch porree schalotte"),
        ("Knoblauch", "🧄", "knoblauch"),
        ("Brokkoli", "🥦", "brokkoli broccoli blumenkohl kohl wirsing"),
        ("Salat", "🥬", "salat spinat rucola mangold"),
        ("Aubergine", "🍆", "aubergine"),
        ("Mais", "🌽", "mais"),
        ("Champignons", "🍄", "pilz champignon pfifferling"),
        (
            "Kräuter",
            "🌿",
            "kräuter basilikum petersilie schnittlauch minze ingwer koriander dill rosmarin thymian",
        ),
        ("Nüsse", "🥜", "nuss nüsse mandel cashew pistazie"),
        ("Spargel", "🥬", "spargel sellerie fenchel"),
        ("Radieschen", "🥕", "radieschen rettich bete"),
        ("Kürbis", "🎃", "kürbis hokkaido"),
    ),
    "bakery": (
        ("Brot", "🍞", "brot toast"),
        ("Brötchen", "🥖", "brötchen semmel baguette schrippe ciabatta buns"),
        ("Croissants", "🥐", "croissant"),
        ("Brezeln", "🥨", "brezel breze laugen"),
        ("Kuchen", "🍰", "kuchen torte zwieback"),
        ("Muffins", "🧁", "muffin"),
        ("Donuts", "🍩", "donut berliner krapfen"),
        ("Wraps", "🫓", "wrap tortilla fladenbrot pita"),
    ),
    "dairy": (
        ("Milch", "🥛", "milch alpro landliebe bärenmarke"),
        ("Käse", "🧀", "käse gouda emmentaler parmesan mozzarella feta camembert brie halloumi philadelphia"),
        ("Butter", "🧈", "butter margarine rama"),
        ("Eier", "🥚", "eier"),
        ("Joghurt", "🥛", "joghurt jogurt quark skyr kefir"),
        ("Pudding", "🍮", "pudding milchreis"),
        ("Tofu", "🧈", "tofu"),
        ("Sahne", "🥛", "sahne schmand crème fraîche fraiche"),
    ),
    "meat": (
        ("Hähnchen", "🍗", "hähnchen hühnchen huhn chicken pute geflügel"),
        ("Hackfleisch", "🥩", "hack fleisch rind schwein steak schnitzel gulasch frikadelle"),
        ("Wurst", "🌭", "wurst würstchen salami schinken aufschnitt leberkäse rügenwalder"),
        ("Speck", "🥓", "speck bacon"),
        ("Lachs", "🐟", "fisch lachs forelle hering kabeljau"),
        ("Garnelen", "🦐", "garnele shrimp krabbe"),
    ),
    "pantry": (
        (
            "Nudeln",
            "🍝",
            "nudel pasta spaghetti penne fusilli lasagne tortellini gnocchi barilla miracoli ravioli",
        ),
        ("Reis", "🍚", "reis couscous bulgur quinoa"),
        ("Mehl", "🌾", "mehl"),
        ("Zucker", "🍬", "zucker"),
        ("Salz", "🧂", "salz pfeffer gewürz paprikapulver zimt curry oregano"),
        ("Olivenöl", "🫒", "öl essig olive balsamico"),
        ("Kidneybohnen", "🥫", "dose konserve bohne linse kichererbse passierte"),
        ("Ketchup", "🥫", "soße sauce ketchup senf mayo pesto tomatenmark"),
        ("Müsli", "🥣", "müsli flocken cornflakes granola kellogg"),
        ("Marmelade", "🍯", "marmelade konfitüre honig nutella aufstrich erdnussbutter sirup"),
        ("Gemüsebrühe", "🍲", "brühe suppe maggi knorr"),
        ("Backpulver", "🧁", "backpulver hefe vanille stärke"),
        ("Apfelmus", "🍎", "apfelmus"),
        ("Sauerkraut", "🫙", "sauerkraut gewürzgurke kokosmilch"),
    ),
    "frozen": (
        ("Pizza", "🍕", "pizza ristorante"),
        ("Pommes", "🍟", "pommes krokette"),
        ("Erbsen", "🥦", "erbse"),
        ("Eis", "🍨", "eis eiscreme magnum langnese ben_&_jerry"),
        ("Eiswürfel", "🧊", "eiswürfel tiefkühl"),
        ("Fischstäbchen", "🐟", "fischstäbchen"),
    ),
    "sweets": (
        (
            "Schokolade",
            "🍫",
            "schokolade schoko praline riegel milka ritter_sport bueno duplo hanuta knoppers toffifee merci raffaello rocher twix snickers mars kitkat",
        ),
        ("Kekse", "🍪", "keks cookie waffel oreo leibniz prinzenrolle"),
        (
            "Gummibärchen",
            "🍬",
            "gummibär bonbon fruchtgummi lakritz kaugummi marshmallow haribo tic_tac mentos werther",
        ),
        ("Chips", "🍿", "chips flips popcorn salzstangen cracker studentenfutter pringles funny"),
    ),
    "drinks": (
        ("Wasser", "💧", "wasser sprudel volvic gerolsteiner"),
        ("Orangensaft", "🧃", "saft schorle capri durstlöscher granini hohes_c"),
        (
            "Cola",
            "🥤",
            "cola limo fanta sprite eistee energy smoothie spezi mezzo schweppes club-mate fritz-kola red_bull monster lipton",
        ),
        ("Kaffee", "☕", "kaffee espresso kaffeebohne kakao jacobs nescafé dallmayr tchibo nesquik"),
        ("Tee", "🍵", "tee"),
        ("Bier", "🍺", "bier radler krombacher beck's augustiner erdinger bitburger warsteiner radeberger"),
        ("Wein", "🍷", "wein sekt prosecco rosé"),
        ("Schnaps", "🥃", "schnaps wodka whisky likör jägermeister aperol"),
    ),
    "household": (
        (
            "Toilettenpapier",
            "🧻",
            "toilettenpapier klopapier küchenrolle taschentuch taschentücher tempo zewa",
        ),
        ("Duschgel", "🧼", "seife duschgel shampoo spülung dove"),
        (
            "Spülmittel",
            "🧽",
            "spülmittel spülmaschine schwamm schwämme spültuch spültücher putzmittel reiniger pril somat finish domestos meister_proper swiffer",
        ),
        ("Waschmittel", "🧺", "waschmittel weichspüler ariel persil lenor"),
        ("Zahnpasta", "🪥", "zahnpasta zahnbürste zahnseide elmex colgate"),
        ("Sonnencreme", "🧴", "creme lotion deo watte tampon feuchttuch feuchttücher nivea labello"),
        ("Rasierer", "🪒", "rasier"),
        ("Müllbeutel", "🗑️", "müllbeutel mülltüte alufolie frischhaltefolie backpapier gefrierbeutel"),
        ("Batterien", "🔋", "batterie"),
        ("Glühbirnen", "💡", "glühbirne leuchtmittel"),
        ("Teelichter", "🕯️", "teelicht kerze"),
        ("Grillkohle", "🔥", "grillkohle streichholz streichhölzer"),
        ("Windeln", "🍼", "windel babynahrung pampers"),
        ("Katzenfutter", "🐾", "futter streu whiskas"),
        ("Pflaster", "💊", "tablette medikament pflaster"),
        ("Blumen", "💐", "blume"),
    ),
}

# Everyday products offered in the name field's suggestion dropdown, listed under the aisle they
# must classify into (a test holds every name to that, so extending the list also checks stems).
PRODUCTS = {
    "produce": """
        Äpfel, Bananen, Birnen, Orangen, Mandarinen, Clementinen, Zitronen, Limetten, Grapefruit,
        Weintrauben, Erdbeeren, Himbeeren, Heidelbeeren, Brombeeren, Johannisbeeren, Kirschen,
        Pflaumen, Zwetschgen, Pfirsiche, Nektarinen, Aprikosen, Mango, Ananas, Wassermelone,
        Honigmelone, Kiwis, Granatapfel, Feigen, Datteln, Avocados, Tomaten, Cherrytomaten,
        Rispentomaten, Gurke, Salatgurke, Zucchini, Paprika, Spitzpaprika, Chili, Peperoni,
        Karotten, Möhren, Kartoffeln, Festkochende Kartoffeln, Süßkartoffeln, Zwiebeln,
        Rote Zwiebeln, Frühlingszwiebeln, Schalotten, Knoblauch, Lauch, Brokkoli, Blumenkohl,
        Rosenkohl, Rotkohl, Weißkohl, Wirsing, Grünkohl, Kohlrabi, Spinat, Babyspinat, Feldsalat,
        Eisbergsalat, Kopfsalat, Romanasalat, Rucola, Mangold, Aubergine, Mais, Champignons,
        Steinpilze, Pfifferlinge, Basilikum, Petersilie, Schnittlauch, Minze, Koriander, Dill,
        Rosmarin, Thymian, Ingwer, Sellerie, Staudensellerie, Fenchel, Rote Bete, Radieschen,
        Rettich, Spargel, Kürbis, Hokkaido, Walnüsse, Haselnüsse, Erdnüsse, Mandeln, Cashewkerne,
        Pistazien, Rosinen
    """,
    "bakery": """
        Brot, Vollkornbrot, Roggenbrot, Toastbrot, Knäckebrot, Brötchen, Vollkornbrötchen,
        Aufbackbrötchen, Milchbrötchen, Laugenbrezeln, Baguette, Ciabatta, Croissants, Zwieback,
        Kuchen, Muffins, Donuts, Berliner, Tortillas, Wraps, Fladenbrot, Pita, Burger Buns
    """,
    "dairy": """
        Milch, Vollmilch, Fettarme Milch, H-Milch, Hafermilch, Mandelmilch, Sojamilch, Buttermilch,
        Butter, Margarine, Eier, Joghurt, Naturjoghurt, Griechischer Joghurt, Fruchtjoghurt,
        Quark, Magerquark, Skyr, Sahne, Schlagsahne, Saure Sahne, Schmand, Crème fraîche,
        Frischkäse, Hüttenkäse, Mozzarella, Gouda, Emmentaler, Parmesan, Feta, Camembert, Brie,
        Reibekäse, Scheibenkäse, Halloumi, Pudding, Milchreis, Kefir, Tofu, Rama, Philadelphia,
        Alpro Haferdrink, Landliebe Milch, Bärenmarke
    """,
    "meat": """
        Hähnchenbrust, Hähnchenschenkel, Putenbrust, Hackfleisch, Rinderhack, Gemischtes Hack,
        Rindersteak, Schweinefilet, Schnitzel, Gulasch, Bratwurst, Wiener Würstchen, Salami,
        Kochschinken, Aufschnitt, Leberwurst, Leberkäse, Speck, Bacon, Frikadellen, Lachs,
        Räucherlachs, Thunfisch, Forelle, Garnelen, Hering, Kabeljau, Seelachs, Rügenwalder Mühle
    """,
    "pantry": """
        Nudeln, Spaghetti, Penne, Fusilli, Lasagneplatten, Tortellini, Gnocchi, Reis, Basmatireis,
        Risottoreis, Couscous, Bulgur, Quinoa, Linsen, Kichererbsen, Kidneybohnen, Weiße Bohnen,
        Tomatenmark, Mehl, Weizenmehl, Dinkelmehl, Paniermehl, Zucker, Puderzucker,
        Vanillezucker, Salz, Pfeffer, Paprikapulver, Zimt, Curry, Oregano, Olivenöl, Rapsöl,
        Sonnenblumenöl, Essig, Balsamico, Ketchup, Senf, Mayonnaise, Pesto, Tomatensoße,
        Sojasoße, Gemüsebrühe, Hühnerbrühe, Müsli, Haferflocken, Cornflakes, Granola, Marmelade,
        Honig, Nutella, Erdnussbutter, Ahornsirup, Backpulver, Hefe, Speisestärke, Apfelmus,
        Sauerkraut, Gewürzgurken, Oliven, Kokosmilch, Barilla Spaghetti, Miracoli, Ravioli, Maggi,
        Knorr Fix, Kellogg's Frosties
    """,
    "frozen": """
        Pizza, Tiefkühlpizza, Pommes, Kroketten, Fischstäbchen, Eis, Vanilleeis, Eiswürfel,
        Tiefkühlgemüse, Erbsen, Ristorante Pizza, Magnum, Langnese, Ben & Jerry's
    """,
    "sweets": """
        Schokolade, Vollmilchschokolade, Zartbitterschokolade, Pralinen, Schokoriegel,
        Müsliriegel, Kekse, Butterkekse, Waffeln, Gummibärchen, Fruchtgummi, Lakritz, Bonbons,
        Kaugummi, Chips, Tortilla-Chips, Popcorn, Salzstangen, Erdnussflips, Cracker,
        Studentenfutter, Marshmallows, Haribo Goldbären, Milka, Ritter Sport, Kinder Schokolade,
        Kinder Bueno, Duplo, Hanuta, Knoppers, Toffifee, Merci, Raffaello, Ferrero Rocher, Twix,
        Snickers, Mars, KitKat, Oreo, Leibniz Butterkeks, Prinzenrolle, Pringles,
        Funny-Frisch Chips, Tic Tac, Mentos, Werther's Original
    """,
    "drinks": """
        Wasser, Mineralwasser, Stilles Wasser, Sprudel, Apfelsaft, Orangensaft, Multivitaminsaft,
        Traubensaft, Apfelschorle, Cola, Cola Zero, Limonade, Eistee, Energydrink, Smoothie,
        Kaffee, Kaffeebohnen, Kaffeepads, Espresso, Tee, Grüner Tee, Früchtetee, Pfefferminztee,
        Kräutertee, Kakao, Bier, Alkoholfreies Bier, Radler, Weißwein, Rotwein, Rosé, Sekt,
        Prosecco, Wodka, Whisky, Paulaner Spezi, Spezi, Mezzo Mix, Coca-Cola, Coca-Cola Zero, Fanta,
        Sprite, Schweppes, Club-Mate, Fritz-Kola, Red Bull, Monster Energy, Capri-Sun, Durstlöscher,
        Lipton Ice Tea, Granini, Hohes C, Volvic, Gerolsteiner, Krombacher, Beck's, Augustiner,
        Paulaner Weißbier, Erdinger, Bitburger, Warsteiner, Radeberger, Jacobs Krönung, Nescafé,
        Dallmayr Prodomo, Tchibo Kaffee, Nesquik, Jägermeister, Aperol
    """,
    "household": """
        Toilettenpapier, Küchenrolle, Taschentücher, Spülmittel, Spülmaschinentabs, Schwämme,
        Spültücher, Allzweckreiniger, WC-Reiniger, Glasreiniger, Waschmittel, Weichspüler,
        Müllbeutel, Alufolie, Frischhaltefolie, Backpapier, Gefrierbeutel, Duschgel, Shampoo,
        Spülung, Handseife, Zahnpasta, Zahnbürste, Zahnseide, Deo, Rasierer, Rasierschaum,
        Bodylotion, Handcreme, Sonnencreme, Wattepads, Wattestäbchen, Tampons, Feuchttücher,
        Windeln, Babynahrung, Katzenfutter, Hundefutter, Katzenstreu, Batterien, Glühbirnen,
        Kerzen, Teelichter, Pflaster, Kopfschmerztabletten, Blumen, Grillkohle, Streichhölzer,
        Tempo, Zewa, Ariel, Persil, Lenor, Pril, Somat, Finish, Domestos, Meister Proper, Swiffer,
        Dove, Elmex, Colgate, Nivea, Labello, Pampers, Whiskas
    """,
}

CATEGORY_LABELS = {key: (label, emoji) for key, label, emoji in CATEGORIES}
_STEMS = tuple(
    (stem.replace("_", " ").casefold(), category, emoji)
    for category, entries in CATALOG.items()
    for _name, emoji, stems in entries
    for stem in stems.split()
)


def classify_item(name):
    """(category key, emoji) for an item name; unknown names land in "Sonstiges".

    German compounds put the deciding word last ("Erdbeermarmelade" is jam, "Schweinefleisch"
    is meat), so the stem ending furthest right wins, and on a tie the longer one ("reis" over
    "eis"). ponytail: substring heuristic, a misfiled item only lands in the wrong aisle.
    """
    text = name.casefold()
    best_key, best = None, (FALLBACK_CATEGORY, CATEGORY_LABELS[FALLBACK_CATEGORY][1])
    for stem, category, emoji in _STEMS:
        position = text.rfind(stem)
        if position < 0:
            continue
        key = (position + len(stem), len(stem))
        if best_key is None or key > best_key:
            best_key, best = key, (category, emoji)
    return best


def group_items(items):
    """Open items grouped by aisle in store order, plus the ticked ones as a flat list.

    Sets `category` and `emoji` on every item for the template.
    """
    open_by_category, done = {}, []
    for item in items:
        item.category, item.emoji = classify_item(item.name)
        if item.is_done:
            done.append(item)
        else:
            open_by_category.setdefault(item.category, []).append(item)
    groups = [
        {"key": key, "label": label, "emoji": emoji, "items": open_by_category[key]}
        for key, label, emoji in CATEGORIES
        if key in open_by_category
    ]
    return groups, done


def _suggestion(name):
    category, emoji = classify_item(name)
    return {"name": name, "emoji": emoji, "aisle": CATEGORY_LABELS[category][0]}


# For the name field's suggestion dropdown (shopping.js).
SUGGESTIONS = [
    _suggestion(name)
    for name in sorted(
        {name.strip() for names in PRODUCTS.values() for name in names.split(",")}
        | {name for entries in CATALOG.values() for name, _emoji, _stems in entries}
    )
]
