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
# "_" stands for a space ("red_bull"); a leading one anchors the stem at a word start ("_gin" finds
# "Gin Tonic" but not "Original").
CATALOG = {
    "produce": (
        ("Äpfel", "🍎", "apfel äpfel obst quitte"),
        ("Birnen", "🍐", "birne"),
        ("Bananen", "🍌", "banane"),
        ("Orangen", "🍊", "orange apfelsine mandarine clementine grapefruit blutorange"),
        ("Zitronen", "🍋", "zitrone limette"),
        ("Weintrauben", "🍇", "traube"),
        ("Erdbeeren", "🍓", "erdbeere"),
        ("Himbeeren", "🍓", "beere himbeere stachelbeere"),
        ("Heidelbeeren", "🫐", "heidelbeere blaubeere brombeere"),
        ("Johannisbeeren", "🍒", "johannisbeere preiselbeere cranberry cranberr"),
        ("Kirschen", "🍒", "kirsche"),
        ("Pfirsiche", "🍑", "pfirsich nektarine aprikose"),
        ("Pflaumen", "🫐", "pflaume zwetschge mirabelle"),
        ("Datteln", "🌴", "dattel feige"),
        ("Rosinen", "🍇", "rosine trockenfrüchte trockenobst"),
        ("Mango", "🥭", "mango"),
        ("Ananas", "🍍", "ananas"),
        ("Kokosnuss", "🥥", "kokosnuss"),
        (
            "Papaya",
            "🥭",
            (
                "papaya maracuja passionsfrucht kaki litschi physalis drachenfrucht sternfrucht guave "
                "kumquat cantaloupe"
            ),
        ),
        ("Melone", "🍉", "melone"),
        ("Kiwis", "🥝", "kiwi"),
        ("Avocados", "🥑", "avocado"),
        ("Tomaten", "🍅", "tomate"),
        ("Gurke", "🥒", "gurke zucchini"),
        ("Zuckerschoten", "🫛", "zuckerschote grüne_bohne edamame sprosse"),
        ("Paprika", "🫑", "paprika"),
        ("Chili", "🌶️", "chili peperoni jalapeño jalapeno"),
        ("Karotten", "🥕", "karotte möhre"),
        ("Kartoffeln", "🥔", "kartoffel"),
        ("Süßkartoffeln", "🍠", "süßkartoffel"),
        ("Zwiebeln", "🧅", "zwiebel lauch porree schalotte"),
        ("Knoblauch", "🧄", "knoblauch"),
        (
            "Brokkoli",
            "🥦",
            ("brokkoli broccoli blumenkohl kohl wirsing chinakohl spitzkohl romanesco gemüse grünkohl"),
        ),
        ("Salat", "🥬", "salat spinat rucola mangold lollo endivie portulak romana"),
        ("Aubergine", "🍆", "aubergine"),
        ("Mais", "🌽", "mais"),
        ("Champignons", "🍄", "pilz champignon pfifferling seitling shiitake"),
        (
            "Kräuter",
            "🌿",
            (
                "kräuter basilikum petersilie schnittlauch minze koriander dill rosmarin thymian kresse "
                "salbei zitronengras suppengrün"
            ),
        ),
        (
            "Ingwer",
            "🫚",
            "ingwer",
        ),
        (
            "Nüsse",
            "🥜",
            ("nuss nüsse mandel cashew pistazie macadamia pinienkern kern samen marone kastanie sesam"),
        ),
        ("Spargel", "🥬", "spargel sellerie fenchel"),
        ("Radieschen", "🥕", "radieschen rettich bete pastinake wurzel topinambur rübe"),
        ("Kürbis", "🎃", "kürbis hokkaido"),
        ("Rhabarber", "🥬", "rhabarber chicorée radicchio pak_choi okra artischocke"),
    ),
    "bakery": (
        (
            "Brot",
            "🍞",
            (
                "brot toast zwieback pumpernickel mestemacher sandwich focaccia naan wasa "
                "leicht_&_cross urkorn crouton"
            ),
        ),
        ("Brötchen", "🥖", "brötchen semmel baguette schrippe ciabatta buns bagel brioche"),
        ("Croissants", "🥐", "croissant"),
        ("Brezeln", "🥨", "brezel breze laugen"),
        (
            "Kuchen",
            "🍰",
            (
                "kuchen torte schnecke plunder strudel bienenstich brownie stollen nussecke hefezopf "
                "tarte windbeutel eclair éclair amerikaner coppenrath"
            ),
        ),
        ("Muffins", "🧁", "muffin"),
        ("Donuts", "🍩", "donut berliner krapfen quarkbällchen"),
        ("Wraps", "🫓", "wrap tortilla fladenbrot pita"),
        ("Pfannkuchen", "🥞", "pfannkuchen crêpe crepe pancake kaiserschmarrn"),
    ),
    "dairy": (
        (
            "Milch",
            "🥛",
            (
                "milch alpro landliebe bärenmarke oatly kondensmilch haferdrink sojadrink mandeldrink "
                "reisdrink"
            ),
        ),
        (
            "Käse",
            "🧀",
            (
                "käse gouda emmentaler parmesan mozzarella feta camembert brie halloumi philadelphia "
                "mascarpone ricotta burrata tilsiter leerdammer babybel grünländer bresso exquisa "
                "almette edamer appenzeller gruyère gruyere manchego pecorino grana_padano gorgonzola "
                "roquefort harzer limburger obatzda milram"
            ),
        ),
        ("Butter", "🧈", "butter margarine rama kerrygold lätta becel ghee schmalz"),
        ("Eier", "🥚", "eier"),
        (
            "Joghurt",
            "🥛",
            (
                "joghurt jogurt quark skyr kefir ayran actimel activia danone ehrmann almighurt froop "
                "fruchtzwerg zott monte müller bauer"
            ),
        ),
        ("Pudding", "🍮", "pudding milchreis pingui milchschnitte"),
        ("Tofu", "🧈", "tofu seitan"),
        ("Sahne", "🥛", "sahne schmand crème fraîche fraiche cremefine rahm sour_cream crème_double"),
        (
            "Kartoffelsalat",
            "🥗",
            ("kartoffelsalat nudelsalat fleischsalat eiersalat thunfischsalat krautsalat tzatziki hummus"),
        ),
        ("Blätterteig", "🥐", "teig"),
    ),
    "meat": (
        (
            "Hähnchen",
            "🍗",
            ("hähnchen hühnchen huhn chicken pute geflügel wiesenhof entenbrust entenkeule gans gänse"),
        ),
        (
            "Hackfleisch",
            "🥩",
            (
                "hack fleisch rind schwein steak schnitzel gulasch frikadelle roulade lamm kotelett "
                "spareribs pork cevapcici gyros patties mett wild rehrücken rehkeule hirsch kassler "
                "kasseler braten tafelspitz entrecôte entrecote ribeye geschnetzelt schaschlik spieß "
                "leber cordon_bleu burger"
            ),
        ),
        (
            "Würstchen",
            "🌭",
            (
                "wurst würstchen krakauer meica böklunder lyoner landjäger cabanossi kabanos "
                "pfefferbeißer bifi frankfurter merguez"
            ),
        ),
        (
            "Aufschnitt",
            "🥪",
            (
                "salami schinken aufschnitt leberwurst leberkäse rügenwalder chorizo mortadella "
                "fleischkäse corned_beef herta gutfried"
            ),
        ),
        ("Speck", "🥓", "speck bacon"),
        (
            "Lachs",
            "🐟",
            (
                "fisch lachs forelle hering kabeljau matjes makrele sardine dorade zander pangasius "
                "rollmops surimi barsch heilbutt steinbutt scholle tilapia karpfen kaviar"
            ),
        ),
        ("Garnelen", "🦐", "garnele shrimp krabbe"),
        ("Muscheln", "🦪", "muschel auster hummer"),
        ("Calamari", "🦑", "calamari tintenfisch"),
        ("Sushi", "🍣", "sushi"),
    ),
    "pantry": (
        (
            "Nudeln",
            "🍝",
            (
                "nudel pasta spaghetti penne fusilli lasagne tortellini gnocchi barilla miracoli "
                "ravioli spirelli farfalle tagliatelle makkaroni rigatoni spätzle ramen yum_yum "
                "cannelloni"
            ),
        ),
        ("Reis", "🍚", "reis couscous bulgur quinoa polenta grieß hirse uncle_ben oryza reispapier nori"),
        ("Mehl", "🌾", "mehl brösel"),
        ("Zucker", "🍬", "zucker kandis süßstoff stevia xylit erythrit"),
        (
            "Salz",
            "🧂",
            (
                "salz pfeffer gewürz paprikapulver zimt curry oregano muskatnuss lorbeer kümmel kurkuma "
                "chiliflocken pulver ostmann gewürzmischung würzmischung anis kardamom safran masala"
            ),
        ),
        ("Olivenöl", "🫒", "öl essig olive balsamico bertolli"),
        (
            "Kidneybohnen",
            "🥫",
            (
                "dose konserve bohne linse kichererbse passierte passierte_tomate gehackte_tomate "
                "dosentomate eintopf terrine erasco pizzatomate mutti gericht"
            ),
        ),
        (
            "Ketchup",
            "🥫",
            (
                "soße sauce ketchup senf mayo pesto tomatenmark heinz develey kühne remoulade "
                "hollandaise tabasco sriracha sambal teriyaki thomy currypaste bolognese arrabbiata "
                "ajvar salsa guacamole aioli dressing salatcreme bautz wasabi miso tahini fondor"
            ),
        ),
        (
            "Müsli",
            "🥣",
            (
                "müsli flocken cornflakes granola kellogg kölln seitenbacher porridge cini_minis "
                "krispies smacks frosties cerealien cheerios uncle_ben"
            ),
        ),
        (
            "Marmelade",
            "🍯",
            (
                "marmelade konfitüre honig nutella aufstrich erdnussbutter sirup gelee schwartau zentis "
                "nudossi dicksaft nougat-creme nougatcreme schokocreme nusscreme bonne_maman"
            ),
        ),
        ("Gemüsebrühe", "🍲", "brühe suppe maggi knorr brühwürfel"),
        (
            "Backpulver",
            "🧁",
            (
                "backpulver hefe vanille stärke mischung puddingpulver gelatine tortenguss oblate "
                "streusel kuvertüre kokosraspel natron backin oetker mondamin backkakao schokotropfen "
                "zuckerguss lebensmittelfarbe"
            ),
        ),
        ("Apfelmus", "🍎", "apfelmus"),
        ("Kartoffelpüree", "🥔", "püree pfanni knödel kloß klöße"),
        (
            "Sauerkraut",
            "🫙",
            ("sauerkraut gewürzgurke kokosmilch antipasti kaper essiggurke cornichon hengstenberg"),
        ),
    ),
    "frozen": (
        ("Pizza", "🍕", "pizza ristorante flammkuchen wagner gustavo pizzabaguette"),
        ("Pommes", "🍟", "pommes krokette wedges rösti puffer nugget mccain agrarfrost"),
        ("Kräuterbaguette", "🥖", "kräuterbaguette"),
        ("Frühlingsrollen", "🥟", "frühlingsrolle dumpling"),
        ("Erbsen", "🫛", "erbse"),
        ("Rahmspinat", "🥬", "rahmspinat gemüsepfanne tiefkühlgemüse"),
        (
            "Eis",
            "🍨",
            (
                "eis eiscreme magnum langnese ben_&_jerry cornetto nogger solero häagen schöller "
                "grünofant calippo viennetta carte_d'or frozen_yogurt"
            ),
        ),
        ("Eiswürfel", "🧊", "eiswürfel tiefkühl crushed_ice"),
        ("Fischstäbchen", "🐟", "fischstäbchen schlemmerfilet backfisch iglo frosta"),
    ),
    "sweets": (
        (
            "Schokolade",
            "🍫",
            (
                "schokolade schoko praline riegel milka ritter_sport bueno duplo hanuta knoppers "
                "toffifee merci raffaello rocher twix snickers mars kitkat küsschen lindt lindor "
                "toblerone after_eight celebrations mon_chéri bounty milky m&m smarties maltesers "
                "balisto yogurette schogetten moser_roth überraschung marzipan nougat corny kinder "
                "ferrero giotto daim lion adventskalender osterhase weihnachtsmann krokant halloren "
                "mozartkugel crossies nippon dickmann schaumkuss schaumküsse"
            ),
        ),
        (
            "Kekse",
            "🍪",
            (
                "keks cookie oreo leibniz prinzenrolle pick_up bahlsen lambertz spekulatius printen "
                "dominostein zimtstern kipferl makrone lotus biscoff prinzen_rolle"
            ),
        ),
        ("Waffeln", "🧇", "waffel"),
        (
            "Gummibärchen",
            "🍬",
            (
                "gummibär bonbon fruchtgummi lakritz kaugummi marshmallow haribo tic_tac mentos werther "
                "gummi nimm_2 maoam trolli katjes mamba hitschler fisherman ricola eukal wrigley "
                "airwaves orbit hubba lakritzschnecke salmiak weingummi saure_zunge colafläschchen "
                "lutscher lolli chupa brause zuckerwatte brausepulver"
            ),
        ),
        (
            "Chips",
            "🍿",
            ("chips flips popcorn pringles funny lay's chio lorenz pom-bär nacho bake_rolls kelly pombär"),
        ),
        ("Salzstangen", "🥨", "salzstange cracker nicnac _tuc _ritz grissini"),
        ("Studentenfutter", "🥜", "studentenfutter ültje seeberger nussmischung"),
    ),
    "drinks": (
        (
            "Wasser",
            "💧",
            (
                "wasser sprudel volvic gerolsteiner adelholzener apollinaris vilbeler evian pellegrino "
                "vittel saskia selters rosbacher lichtenauer teinacher fachingen römerquelle ensinger "
                "black_forest _vio"
            ),
        ),
        (
            "Orangensaft",
            "🧃",
            (
                "saft schorle capri durstlöscher granini hohes_c valensina rauch innocent true_fruits "
                "punica christinen"
            ),
        ),
        (
            "Cola",
            "🥤",
            (
                "cola limo fanta sprite eistee energy smoothie spezi mezzo schweppes club-mate fritz- "
                "kola red_bull monster lipton pepsi 7up mirinda bionade almdudler bitter_lemon "
                "ginger_ale tonic pfanner arizona fuze rockstar club_mate ginger coke _lift sinalco "
                "orangina rivella dr_pepper mountain_dew gatorade powerade isostar thomas_henry fever- "
                "tree lemonaid charitea wostok kombucha mio_mio"
            ),
        ),
        (
            "Kaffee",
            "☕",
            (
                "kaffee espresso kaffeebohne kakao jacobs nescafé dallmayr tchibo nesquik lavazza "
                "melitta eduscho senseo nespresso dolce_gusto cappuccino macchiato kaba ovomaltine "
                "espressobohne kaffeepulver kakaopulver trinkschokolade heiße_schokolade segafredo "
                "_illy starbucks kaffee_hag onko cafissimo tassimo"
            ),
        ),
        (
            "Tee",
            "🍵",
            (
                "tee teekanne meßmer pompadour teebeutel chai matcha earl_grey kamille rooibos yogi "
                "milford goldmännchen"
            ),
        ),
        (
            "Bier",
            "🍺",
            (
                "bier radler krombacher beck's augustiner erdinger bitburger warsteiner radeberger "
                "becks veltins oettinger jever flensburger hasseröder köstritzer franziskaner pschorr "
                "bräu tegernseer corona heineken desperados kölsch pils helles malz paulaner gaffel "
                "früh_kölsch reissdorf sion diebels schöfferhofer benediktiner maisel rothaus ayinger "
                "spaten hofbräu kulmbacher lübzer störtebeker astra holsten carlsberg tuborg grolsch "
                "budweiser urquell guinness kilkenny clausthaler licher wernesgrüner feldschlößchen "
                "krostitzer tannenzäpfle pilsener paderborner herforder einbecker stella_artois cider "
                "strongbow somersby mixery weisse"
            ),
        ),
        (
            "Wein",
            "🍷",
            (
                "wein rosé riesling burgunder chardonnay merlot primitivo sauvignon pinot cabernet "
                "shiraz tempranillo montepulciano lambrusco chianti rioja dornfelder silvaner thurgau "
                "sherry sangria federweißer"
            ),
        ),
        (
            "Sekt",
            "🥂",
            (
                "sekt prosecco champagner rotkäppchen freixenet mumm henkell hugo lillet spritz cava "
                "crémant cremant schaumwein"
            ),
        ),
        (
            "Schnaps",
            "🥃",
            (
                "schnaps wodka whisky likör jägermeister aperol rum baileys amaretto ramazzotti sambuca "
                "ouzo tequila doppelkorn berliner_luft asbach weinbrand edelkirsch pfeffi feigling "
                "bombay gordon hendrick vodka bacardi captain_morgan havana smirnoff gorbatschow "
                "absolut jack_daniel jim_beam johnnie_walker jameson ballantine malibu licor kahlúa "
                "kahlua cointreau grappa _korn obstler eierlikör verpoorten batida absinth underberg "
                "kümmerling killepitsch bourbon cognac brandy mezcal"
            ),
        ),
        ("Gin", "🍸", "_gin cocktail martini campari"),
    ),
    "household": (
        (
            "Toilettenpapier",
            "🧻",
            ("toilettenpapier klopapier küchenrolle taschentuch taschentücher tempo zewa kleenex hakle"),
        ),
        (
            "Duschgel",
            "🧼",
            (
                "seife duschgel shampoo spülung dove head_&_shoulders pantene schauma garnier l'oréal "
                "syoss balea haarkur haargel haarfarbe haargummi duschdas badezusatz schaumbad "
                "haarbürste kamm trockenshampoo"
            ),
        ),
        (
            "Spülmittel",
            "🧽",
            (
                "spülmittel spülmaschine schwamm schwämme spültuch spültücher putzmittel reiniger pril "
                "somat finish domestos meister_proper swiffer bürste lappen tuch tücher entkalker "
                "klarspüler maschinensalz regeneriersalz wc-stein sagrotan calgon handschuh "
                "desinfektion politur fensterleder mopp besen eimer kehrblech"
            ),
        ),
        (
            "Waschmittel",
            "🧺",
            (
                "waschmittel weichspüler ariel persil lenor vanish perwoll frosch weißer_riese "
                "waschpulver fleckentferner wäscheklammer"
            ),
        ),
        (
            "Zahnpasta",
            "🪥",
            (
                "zahnpasta zahnbürste zahnseide elmex colgate odol listerine blend-a-med sensodyne "
                "oral-b zahnstocher"
            ),
        ),
        (
            "Sonnencreme",
            "🧴",
            (
                "creme lotion deo watte tampon feuchttuch feuchttücher nivea labello spray rexona "
                "parfüm aftershave penaten lippenpflege mascara lippenstift nagellack binde slipeinlage "
                "kondom durex babyöl strumpf puder make-up makeup concealer foundation wimperntusche "
                "kajal eyeliner mizellenwasser gesichtswasser nagelfeile pinzette ohropax raumduft "
                "lufterfrischer febreze air_wick ambi_pur"
            ),
        ),
        ("Rasierer", "🪒", "rasier gillette wilkinson rasierwasser"),
        ("Müllbeutel", "🗑️", "müllbeutel mülltüte gelber_sack gelbe_säcke"),
        ("Kaffeefilter", "☕", "filter"),
        ("Alufolie", "📜", "alufolie frischhaltefolie backpapier gefrierbeutel beutel papier grillschale"),
        ("Batterien", "🔋", "batterie varta duracell akku kabel"),
        ("Glühbirnen", "💡", "glühbirne leuchtmittel lampe"),
        ("Teelichter", "🕯️", "teelicht kerze"),
        ("Grillkohle", "🔥", "grillkohle streichholz streichhölzer anzünder feuerzeug holzkohle"),
        (
            "Windeln",
            "🍼",
            (
                "windel babynahrung pampers babybrei hipp schnuller aptamil milupa bebivita folgemilch "
                "pre-milch babymilch"
            ),
        ),
        (
            "Katzenfutter",
            "🐾",
            (
                "futter streu whiskas felix sheba pedigree frolic kauknochen leckerli royal_canin "
                "purina kitekat cesar chappi dreamies vitakraft"
            ),
        ),
        (
            "Tabletten",
            "💊",
            (
                "tablette medikament hansaplast ibuprofen paracetamol aspirin magnesium vitamin "
                "bepanthen voltaren grippostad augentropfen nasenspray thermometer hustensaft"
            ),
        ),
        ("Pflaster", "🩹", "pflaster mullbinde verband"),
        ("Blumen", "💐", "blume pflanze dünger"),
        ("Servietten", "🍽️", "serviette pappteller pappbecher strohhalm trinkhalm partybecher"),
        (
            "Stifte",
            "✏️",
            (
                "stift kugelschreiber bleistift textmarker klebstoff kleber tesa klebeband "
                "briefumschlag heft notizblock briefmarke schere briefumschläge"
            ),
        ),
        ("Zigaretten", "🚬", "zigarette tabak"),
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
        Pistazien, Rosinen, Blutorangen, Papaya, Maracuja, Kaki, Litschis, Physalis, Stachelbeeren,
        Preiselbeeren, Getrocknete Cranberries, Rhabarber, Kokosnuss, Chicorée, Radicchio,
        Lollo Rosso, Pak Choi, Chinakohl, Spitzkohl, Romanesco, Zuckerschoten, Grüne Bohnen,
        Edamame, Okra, Artischocken, Pastinaken, Petersilienwurzel, Topinambur, Meerrettich,
        Gemüsezwiebeln, Bärlauch, Kresse, Salbei, Zitronengras, Suppengrün, Jalapeños,
        Fleischtomaten, Datteltomaten, Ochsenherztomaten, Kräuterseitlinge, Shiitake, Austernpilze,
        Sojasprossen, Macadamia, Paranüsse, Pekannüsse, Pinienkerne, Sonnenblumenkerne, Kürbiskerne,
        Chiasamen, Leinsamen, Getrocknete Aprikosen, Salatherzen, Bio-Bananen, Babykarotten,
        Mirabellen, Quitten, Drachenfrucht, Sternfrucht, Guave, Kumquats, Cantaloupe-Melone, Gemüse,
        Obst, Suppengemüse, Grillgemüse, Wokgemüse, Endivien, Steckrüben, Maronen, Esskastanien,
        Sesam, Trockenfrüchte, Trockenobst, Snackgurken, Snacktomaten, Gelbe Paprika, Rote Paprika,
        Bundmöhren, Strauchtomaten, Kirschtomaten, Galiamelone, Nashi-Birnen, Mini-Romana,
        Topfbasilikum
    """,
    "bakery": """
        Brot, Vollkornbrot, Roggenbrot, Toastbrot, Knäckebrot, Brötchen, Vollkornbrötchen,
        Aufbackbrötchen, Milchbrötchen, Laugenbrezeln, Baguette, Ciabatta, Croissants, Zwieback,
        Kuchen, Muffins, Donuts, Berliner, Tortillas, Wraps, Fladenbrot, Pita, Burger Buns,
        Dinkelbrot, Mischbrot, Bauernbrot, Sauerteigbrot, Weißbrot, Sonnenblumenbrot, Pumpernickel,
        Körnerbrötchen, Kaiserbrötchen, Laugenbrötchen, Laugenstangen, Franzbrötchen,
        Schokocroissant, Zimtschnecken, Plunder, Apfelstrudel, Käsekuchen, Streuselkuchen,
        Marmorkuchen, Bienenstich, Brownies, Lebkuchen, Stollen, Toastbrötchen, Sandwichtoast,
        Golden Toast, Harry Brot, Mestemacher, Bagels, Brioche, Hamburgerbrötchen, Tortenboden,
        Focaccia, Naan, Wasa Knäckebrot, Leicht & Cross, Lieken Urkorn, Croutons, Nussecken,
        Hefezopf, Tarte, Windbeutel, Eclairs, Amerikaner, Quarkbällchen, Pfannkuchen, Crêpes,
        Pancakes, Kaiserschmarrn, Mohnbrötchen, Sesambrötchen, Rosinenbrötchen, Schrippen, Semmeln,
        Krustenbrot, Kastenbrot, Obstkuchen, Mohnkuchen, Baumkuchen, Christstollen,
        Coppenrath & Wiese
    """,
    "dairy": """
        Milch, Vollmilch, Fettarme Milch, H-Milch, Hafermilch, Mandelmilch, Sojamilch, Buttermilch,
        Butter, Margarine, Eier, Joghurt, Naturjoghurt, Griechischer Joghurt, Fruchtjoghurt, Quark,
        Magerquark, Skyr, Sahne, Schlagsahne, Saure Sahne, Schmand, Crème fraîche, Frischkäse,
        Hüttenkäse, Mozzarella, Gouda, Emmentaler, Parmesan, Feta, Camembert, Brie, Reibekäse,
        Scheibenkäse, Halloumi, Pudding, Milchreis, Kefir, Tofu, Rama, Philadelphia,
        Alpro Haferdrink, Landliebe Milch, Bärenmarke, Laktosefreie Milch, Barista Hafermilch,
        Oatly, Ayran, Trinkjoghurt, Actimel, Activia, Danone, Ehrmann Almighurt, Müllermilch,
        Grießpudding, Kinder Pingui, Milchschnitte, Frischmilch, Kaffeesahne, Kondensmilch,
        Sprühsahne, Kochsahne, Cremefine, Mascarpone, Ricotta, Burrata, Hirtenkäse, Bergkäse,
        Butterkäse, Tilsiter, Leerdammer, Babybel, Grünländer, Bresso, Exquisa, Almette,
        Kräuterquark, Kräuterbutter, Weidebutter, Kerrygold, Lätta, Becel, Freilandeier,
        Wachteleier, Räuchertofu, Seitan, Hummus, Tzatziki, Kartoffelsalat, Nudelsalat,
        Fleischsalat, Eiersalat, Krautsalat, Blätterteig, Pizzateig, Hefeteig, Edamer, Appenzeller,
        Gruyère, Manchego, Pecorino, Grana Padano, Gorgonzola, Roquefort, Harzer Käse, Limburger,
        Obatzda, Raclettekäse, Grillkäse, Ziegenkäse, Schafskäse, Milram, Ghee, Butterschmalz,
        Schmalz, Froop, Fruchtzwerge, Zott Sahnejoghurt, Monte, Müller Joghurt, Bauer Joghurt,
        Sour Cream, Crème double, Schlagrahm, Dickmilch, Proteinpudding, Sojajoghurt, Haferjoghurt,
        Vanillejoghurt, Schwarzwaldmilch, Ziegenfrischkäse
    """,
    "meat": """
        Hähnchenbrust, Hähnchenschenkel, Putenbrust, Hackfleisch, Rinderhack, Gemischtes Hack,
        Rindersteak, Schweinefilet, Schnitzel, Gulasch, Bratwurst, Wiener Würstchen, Salami,
        Kochschinken, Aufschnitt, Leberwurst, Leberkäse, Speck, Bacon, Frikadellen, Lachs,
        Räucherlachs, Thunfisch, Forelle, Garnelen, Hering, Kabeljau, Seelachs, Rügenwalder Mühle,
        Hähnchenbrustfilet, Hähnchenflügel, Chicken Wings, Putenschnitzel, Putenaufschnitt,
        Rinderrouladen, Rinderfilet, Lammkoteletts, Kotelett, Schweinebauch, Nackensteaks,
        Spareribs, Pulled Pork, Cevapcici, Gyros, Burger Patties, Hackbällchen, Zwiebelmett,
        Grillwürstchen, Nürnberger Würstchen, Weißwurst, Currywurst, Bockwurst, Krakauer, Chorizo,
        Serrano Schinken, Parmaschinken, Lachsschinken, Mortadella, Bierschinken, Teewurst,
        Mettwurst, Fleischkäse, Corned Beef, Wiesenhof, Herta, Gutfried, Meica, Böklunder, Matjes,
        Makrele, Sardinen, Dorade, Zander, Pangasius, Tintenfisch, Calamari, Muscheln, Lachsfilet,
        Fischfilet, Rollmops, Surimi, Krabben, Shrimps, Entenbrust, Entenkeulen, Gänsebraten,
        Gänsekeulen, Wildgulasch, Rehrücken, Hirschgulasch, Kassler, Schweinebraten, Rinderbraten,
        Tafelspitz, Entrecôte, Ribeye-Steak, Rumpsteak, Hüftsteak, Minutensteaks, Geschnetzeltes,
        Schaschlik, Grillspieße, Hähnchenleber, Cordon Bleu, Burger, Lyoner, Fleischwurst,
        Jagdwurst, Blutwurst, Landjäger, Cabanossi, Pfefferbeißer, Bifi, Frankfurter Würstchen,
        Thüringer Rostbratwurst, Merguez, Rotbarsch, Viktoriabarsch, Heilbutt, Scholle, Tilapia,
        Karpfen, Kaviar, Austern, Hummer, Jakobsmuscheln, Miesmuscheln, Sushi, Thunfischsteak,
        Forellenfilet, Bismarckhering, Kalbsschnitzel, Grillfleisch
    """,
    "pantry": """
        Nudeln, Spaghetti, Penne, Fusilli, Lasagneplatten, Tortellini, Gnocchi, Reis, Basmatireis,
        Risottoreis, Couscous, Bulgur, Quinoa, Linsen, Kichererbsen, Kidneybohnen, Weiße Bohnen,
        Tomatenmark, Mehl, Weizenmehl, Dinkelmehl, Paniermehl, Zucker, Puderzucker, Vanillezucker,
        Salz, Pfeffer, Paprikapulver, Zimt, Curry, Oregano, Olivenöl, Rapsöl, Sonnenblumenöl, Essig,
        Balsamico, Ketchup, Senf, Mayonnaise, Pesto, Tomatensoße, Sojasoße, Gemüsebrühe,
        Hühnerbrühe, Müsli, Haferflocken, Cornflakes, Granola, Marmelade, Honig, Nutella,
        Erdnussbutter, Ahornsirup, Backpulver, Hefe, Speisestärke, Apfelmus, Sauerkraut,
        Gewürzgurken, Oliven, Kokosmilch, Barilla Spaghetti, Miracoli, Ravioli, Maggi, Knorr Fix,
        Kellogg's Frosties, Vollkornnudeln, Spirelli, Farfalle, Tagliatelle, Makkaroni, Rigatoni,
        Eiernudeln, Spätzle, Glasnudeln, Reisnudeln, Mie-Nudeln, Ramen, Instantnudeln, Yum Yum,
        Cannelloni, Wildreis, Jasminreis, Parboiled Reis, Langkornreis, Polenta, Grieß, Hirse,
        Rote Linsen, Passierte Tomaten, Gehackte Tomaten, Dosentomaten, Erbsensuppe, Linsensuppe,
        Gulaschsuppe, Tomatensuppe, Eintopf, 5 Minuten Terrine, Erasco, Brühwürfel, Backmischung,
        Puddingpulver, Gelatine, Tortenguss, Backoblaten, Zuckerstreusel, Schokostreusel, Kuvertüre,
        Kokosraspeln, Trockenhefe, Natron, Backin, Dr. Oetker, Mondamin, Agavendicksaft,
        Zuckerrübensirup, Gelee, Konfitüre, Schwartau, Zentis, Nuss-Nougat-Creme, Schokocreme,
        Nudossi, Kölln Haferflocken, Seitenbacher, Knuspermüsli, Porridge, Cini Minis,
        Choco Krispies, Smacks, Frosties, Heinz Ketchup, Develey, Kühne, Remoulade, Grillsoße,
        BBQ-Soße, Currysoße, Hollandaise, Bratensoße, Löwensenf, Tabasco, Sriracha, Sambal Oelek,
        Worcestersauce, Teriyaki-Soße, Currypaste, Pesto Rosso, Antipasti, Kapern, Essiggurken,
        Cornichons, Meersalz, Jodsalz, Pfefferkörner, Muskatnuss, Lorbeerblätter, Kreuzkümmel,
        Kurkuma, Chiliflocken, Knoblauchpulver, Currypulver, Ostmann, Thomy, Bertolli, Kokosöl,
        Leinöl, Sesamöl, Bratöl, Apfelessig, Weißweinessig, Semmelbrösel, Kandiszucker, Süßstoff,
        Stevia, Xylit, Erythrit, Rohrzucker, Brauner Zucker, Gelierzucker, Gewürzmischung,
        Sternanis, Kardamom, Safran, Garam Masala, Cayennepfeffer, Chilipulver, Zimtstangen,
        Vanilleschoten, Pizzatomaten, Mutti Passata, Fertiggericht, Bolognese-Soße, Arrabbiata,
        Ajvar, Salsa, Guacamole, Aioli, Salatdressing, Salatcreme, Bautz'ner Senf, Wasabi, Miso,
        Tahini, Fondor, Sojasauce, Fischsauce, Austernsauce, Chilisauce, Pizzasoße, Cerealien,
        Cheerios, Uncle Ben's, Oryza, Sushi-Reis, Reispapier, Nori, Bonne Maman, Backkakao,
        Schokotropfen, Zuckerguss, Lebensmittelfarbe, Hengstenberg, Kartoffelpüree, Pfanni,
        Kartoffelknödel, Semmelknödel, Kartoffelklöße, Tütensuppe
    """,
    "frozen": """
        Pizza, Tiefkühlpizza, Pommes, Kroketten, Fischstäbchen, Eis, Vanilleeis, Eiswürfel,
        Tiefkühlgemüse, Erbsen, Ristorante Pizza, Magnum, Langnese, Ben & Jerry's, Flammkuchen,
        Wagner Pizza, Gustavo Gusto, Pommes Frites, Wedges, Rösti, Kartoffelpuffer, Chicken Nuggets,
        Schlemmerfilet, Backfisch, Iglo, Frosta, Rahmspinat, Gemüsepfanne, Frühlingsrollen,
        Dumplings, Eis am Stiel, Eiskonfekt, Cornetto, Nogger, Solero, Häagen-Dazs, Mövenpick Eis,
        Schöller, Pizzabaguette, McCain Pommes, Agrarfrost, Süßkartoffelpommes, Grünofant, Calippo,
        Viennetta, Carte d'Or, Frozen Yogurt, Eisbecher, Crushed Ice, Kräuterbaguette,
        Dr. Oetker Pizza, Käpt'n Iglo Fischstäbchen
    """,
    "sweets": """
        Schokolade, Vollmilchschokolade, Zartbitterschokolade, Pralinen, Schokoriegel, Müsliriegel,
        Kekse, Butterkekse, Waffeln, Gummibärchen, Fruchtgummi, Lakritz, Bonbons, Kaugummi, Chips,
        Tortilla-Chips, Popcorn, Salzstangen, Erdnussflips, Cracker, Studentenfutter, Marshmallows,
        Haribo Goldbären, Milka, Ritter Sport, Kinder Schokolade, Kinder Bueno, Duplo, Hanuta,
        Knoppers, Toffifee, Merci, Raffaello, Ferrero Rocher, Twix, Snickers, Mars, KitKat, Oreo,
        Leibniz Butterkeks, Prinzenrolle, Pringles, Funny-Frisch Chips, Tic Tac, Mentos,
        Werther's Original, Kinder Riegel, Kinder Überraschung, Ferrero Küsschen, Lindt, Lindor,
        Toblerone, After Eight, Celebrations, Mon Chéri, Bounty, Milky Way, M&M's, Smarties,
        Maltesers, Balisto, Yogurette, Schogetten, Moser Roth, Nimm 2, Maoam, Trolli, Katjes, Mamba,
        Hitschler, Fisherman's Friend, Ricola, Em-eukal, Wrigley's, Airwaves, Orbit, Hubba Bubba,
        Lakritzschnecken, Salmiak, Weingummi, Lay's, Chio Chips, Lorenz Crunchips, Pom-Bär, Nachos,
        Bake Rolls, Reiswaffeln, Choco Leibniz, Pick Up, Bahlsen, Lambertz, Doppelkekse,
        Spekulatius, Printen, Dominosteine, Marzipan, Nougat, Corny, Proteinriegel, Kinder Country,
        Kinder Maxi King, Kinder Joy, Ferrero, Giotto, Daim, Lion, Adventskalender,
        Schoko-Osterhase, Schokoweihnachtsmann, Lindt Goldhase, Krokant, Halloren Kugeln,
        Mozartkugeln, Choco Crossies, Nippon, Dickmann's, Schaumküsse, Zimtsterne, Vanillekipferl,
        Kokosmakronen, Lotus Biscoff, Prinzen Rolle, Saure Zungen, Colafläschchen, Lutscher, Lollis,
        Chupa Chups, Brausepulver, Zuckerwatte, Kelly's Chips, Pombär, Kartoffelchips, Paprikachips,
        Gemüsechips, NicNac's, Tuc, Ritz Cracker, Grissini, Ültje, Seeberger, Nussmischung,
        Hustenbonbons
    """,
    "drinks": """
        Wasser, Mineralwasser, Stilles Wasser, Sprudel, Apfelsaft, Orangensaft, Multivitaminsaft,
        Traubensaft, Apfelschorle, Cola, Cola Zero, Limonade, Eistee, Energydrink, Smoothie, Kaffee,
        Kaffeebohnen, Kaffeepads, Espresso, Tee, Grüner Tee, Früchtetee, Pfefferminztee, Kräutertee,
        Kakao, Bier, Alkoholfreies Bier, Radler, Weißwein, Rotwein, Rosé, Sekt, Prosecco, Wodka,
        Whisky, Paulaner Spezi, Spezi, Mezzo Mix, Coca-Cola, Coca-Cola Zero, Fanta, Sprite,
        Schweppes, Club-Mate, Fritz-Kola, Red Bull, Monster Energy, Capri-Sun, Durstlöscher,
        Lipton Ice Tea, Granini, Hohes C, Volvic, Gerolsteiner, Krombacher, Beck's, Augustiner,
        Paulaner Weißbier, Erdinger, Bitburger, Warsteiner, Radeberger, Jacobs Krönung, Nescafé,
        Dallmayr Prodomo, Tchibo Kaffee, Nesquik, Jägermeister, Aperol, Pepsi, Pepsi Max, 7Up,
        Mirinda, Afri Cola, Bionade, Almdudler, Bitter Lemon, Ginger Ale, Tonic Water,
        Rhabarberschorle, Pfanner, Arizona, Fuze Tea, Rockstar, Club Mate, Adelholzener,
        Apollinaris, Bad Vilbeler, Evian, San Pellegrino, Vittel, Saskia, Selters, Lavazza,
        Melitta Kaffee, Eduscho, Senseo Pads, Nespresso, Dolce Gusto, Kaffeekapseln, Cappuccino,
        Latte Macchiato, Instantkaffee, Eiskaffee, Teekanne, Meßmer, Pompadour, Rooibostee,
        Teebeutel, Kaba, Ovomaltine, Valensina, Rauch, Innocent, True Fruits, Ananassaft,
        Tomatensaft, Kirschsaft, Maracujasaft, Becks, Veltins, Oettinger, Jever, Flensburger,
        Hasseröder, Köstritzer, Franziskaner, Hacker-Pschorr, Löwenbräu, Tegernseer, Corona,
        Heineken, Desperados, Kölsch, Pils, Helles, Weizenbier, Rotkäppchen, Freixenet, Mumm,
        Henkell, Champagner, Hugo, Lillet, Aperol Spritz, Riesling, Grauburgunder, Spätburgunder,
        Chardonnay, Merlot, Primitivo, Glühwein, Rum, Baileys, Amaretto, Ramazzotti, Sambuca, Ouzo,
        Tequila, Doppelkorn, Berliner Luft, Asbach, Weinbrand, Eckes Edelkirsch, Pfeffi,
        Kleiner Feigling, Bombay Sapphire, Gordon's, Hendrick's, Vita Malz, Karamalz, Malzbier,
        Rosbacher, Lichtenauer, Teinacher, Staatl. Fachingen, Römerquelle, Ensinger, Black Forest,
        Vio, Heilwasser, Kokoswasser, Punica, Christinen, Ginger Beer, Coke Zero, Lift, Sinalco,
        Orangina, Rivella, Dr Pepper, Mountain Dew, Gatorade, Powerade, Isostar, Thomas Henry,
        Fever-Tree, Lemonaid, ChariTea, Wostok, Kombucha, Mio Mio Mate, Vita Cola, Sprite Zero,
        Fanta Exotic, Espressobohnen, Kaffeepulver, Milchkaffee, Trinkschokolade, Heiße Schokolade,
        Segafredo, Illy, Starbucks, Kaffee Hag, Onko, Cafissimo, Tassimo, Chai Latte, Matcha,
        Earl Grey, Schwarzer Tee, Kamillentee, Fencheltee, Ingwertee, Rooibos, Yogi Tea, Milford,
        Goldmännchen, Paulaner, Gaffel Kölsch, Früh Kölsch, Reissdorf, Sion Kölsch, Diebels,
        Schöfferhofer, Benediktiner, Maisel's Weisse, Rothaus Tannenzäpfle, Ayinger, Spaten,
        Hofbräu, Kulmbacher, Lübzer, Störtebeker, Astra, Holsten, Carlsberg, Tuborg, Grolsch,
        Budweiser, Pilsner Urquell, Guinness, Kilkenny, Clausthaler, Erdinger Alkoholfrei,
        Beck's Blue, Licher, Wernesgrüner, Feldschlößchen, Ur-Krostitzer, König Pilsener,
        Paderborner, Herforder, Einbecker, Stella Artois, Cider, Strongbow, Somersby, Mixery,
        Weißburgunder, Silvaner, Müller-Thurgau, Dornfelder, Lambrusco, Chianti, Rioja,
        Sauvignon Blanc, Pinot Grigio, Cabernet Sauvignon, Shiraz, Tempranillo, Montepulciano,
        Portwein, Sherry, Sangria, Federweißer, Apfelwein, Cava, Crémant, Kindersekt, Schaumwein,
        Gin, Gin Tonic, Martini, Campari, Bacardi, Captain Morgan, Havana Club, Absolut Vodka,
        Smirnoff, Gorbatschow, Jack Daniel's, Jim Beam, Johnnie Walker, Jameson, Ballantine's,
        Malibu, Licor 43, Kahlúa, Cointreau, Grappa, Korn, Obstler, Eierlikör, Verpoorten,
        Batida de Côco, Absinth, Underberg, Kümmerling, Killepitsch, Bourbon, Cognac, Brandy,
        Mezcal, Kakaopulver, Berliner Weisse
    """,
    "household": """
        Toilettenpapier, Küchenrolle, Taschentücher, Spülmittel, Spülmaschinentabs, Schwämme,
        Spültücher, Allzweckreiniger, WC-Reiniger, Glasreiniger, Waschmittel, Weichspüler,
        Müllbeutel, Alufolie, Frischhaltefolie, Backpapier, Gefrierbeutel, Duschgel, Shampoo,
        Spülung, Handseife, Zahnpasta, Zahnbürste, Zahnseide, Deo, Rasierer, Rasierschaum,
        Bodylotion, Handcreme, Sonnencreme, Wattepads, Wattestäbchen, Tampons, Feuchttücher,
        Windeln, Babynahrung, Katzenfutter, Hundefutter, Katzenstreu, Batterien, Glühbirnen, Kerzen,
        Teelichter, Pflaster, Kopfschmerztabletten, Blumen, Grillkohle, Streichhölzer, Tempo, Zewa,
        Ariel, Persil, Lenor, Pril, Somat, Finish, Domestos, Meister Proper, Swiffer, Dove, Elmex,
        Colgate, Nivea, Labello, Pampers, Whiskas, Spülbürste, Putzlappen, Mikrofasertücher,
        Geschirrtücher, Toilettenreiniger, Entkalker, Essigreiniger, Backofenreiniger,
        Abflussreiniger, Klarspüler, Spülmaschinensalz, Regeneriersalz, WC-Steine, Raumspray,
        Sagrotan, Calgon, Vanish, Perwoll, Frosch, Weißer Riese, Vollwaschmittel, Colorwaschmittel,
        Waschpulver, Fleckentferner, Wäscheklammern, Gummihandschuhe, Biomüllbeutel, Gelbe Säcke,
        Staubsaugerbeutel, Kaffeefilter, Geschenkpapier, Druckerpapier, LED-Lampe, Varta, Duracell,
        Grillanzünder, Feuerzeug, Holzkohle, Head & Shoulders, Pantene, Schauma, Garnier, L'Oréal,
        Syoss, Balea, Haarkur, Haargel, Haarspray, Haarfarbe, Haargummis, Duschdas, Badezusatz,
        Schaumbad, Flüssigseife, Rexona, Deoroller, Rasierklingen, Gillette, Wilkinson, Rasiergel,
        Aftershave, Parfüm, Gesichtscreme, Penaten, Lippenpflege, Abschminktücher, Mascara,
        Lippenstift, Nagellack, Nagellackentferner, Mundspülung, Odol, Listerine, Blend-a-med,
        Sensodyne, Oral-B, Zahnstocher, Damenbinden, Slipeinlagen, Kondome, Durex, Kleenex, Hakle,
        Hansaplast, Desinfektionsmittel, Ibuprofen, Paracetamol, Aspirin, Nasenspray,
        Vitamintabletten, Magnesium, Babybrei, Hipp, Babyöl, Trockenfutter, Nassfutter, Felix,
        Sheba, Pedigree, Frolic, Kauknochen, Hundeleckerli, Vogelfutter, Fischfutter, Blumenerde,
        Haarbürste, Kamm, Trockenshampoo, Möbelpolitur, Fensterleder, Wischmopp, Besen, Eimer,
        Kehrblech, Puder, Make-up, Concealer, Foundation, Wimperntusche, Kajal, Eyeliner,
        Mizellenwasser, Gesichtswasser, Nagelfeile, Pinzette, Ohropax, Raumduft, Lufterfrischer,
        Febreze, Air Wick, Ambi Pur, Duftkerzen, Rasierwasser, Schnuller, Aptamil, Milupa, Bebivita,
        Folgemilch, Pre-Milch, Royal Canin, Purina, Kitekat, Cesar, Chappi, Dreamies, Vitakraft,
        Bepanthen, Voltaren, Grippostad, Halstabletten, Augentropfen, Fieberthermometer, Hustensaft,
        Mullbinden, Verbandszeug, Pflanzen, Dünger, Akkus, Ladekabel, Grillschale, Servietten,
        Pappteller, Pappbecher, Strohhalme, Kugelschreiber, Bleistifte, Textmarker, Klebstoff,
        Tesafilm, Klebeband, Briefumschläge, Hefte, Notizblock, Briefmarken, Schere, Zigaretten,
        Tabak
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
    # Leading space, so a stem starting with "_" only matches at a word start ("_gin").
    text = " " + name.casefold()
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
