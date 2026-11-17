import io
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth.models import User
from django.core.cache import cache
from django.test import SimpleTestCase, TestCase
from PIL import Image

from app.models import WishList, WishListItem
from app.services.safe_fetch import FetchError
from app.services.wishlist import _amount, list_totals, parse_product_page, product_url, title_from_url
from app.tests.helpers import FakeHttpConnection

PRODUCT_PAGE = """<html><head>
<link rel="canonical" href="https://www.amazon.de/kaffeemaschine/dp/B0TEST1234"/>
</head><body>
<span id="productTitle" class="a-size-large">   Kaffeemaschine &amp; Mühle,  Edelstahl   </span>
<a id="acrPopover" class="reviewCountTextLinkedHistogram" title="4,6 von 5 Sternen"></a>
<span id="acrCustomerReviewText" aria-label="1.234 Rezensionen" class="a-size-small">(1.234)</span>
<script>{"desktop_buybox_group_1":[{"displayPrice":"1.299,99 €","priceAmount":1299.99,"currencySymbol":"€"}]}</script>
<div id="availability" class="a-section"><span class="a-size-medium a-color-success primary-availability-message"> Auf Lager </span></div>
<img id="landingImage" data-old-hires="https://m.media-amazon.com/images/I/71abc._AC_SL1500_.jpg" src="x">
</body></html>"""

SHOP_PAGE = """<html><head>
<meta property="og:title" content="Nicht dieser Titel">
<script type="application/ld+json">{"@context": "https://schema.org", "@graph": [
  {"@type": "BreadcrumbList", "name": "Krümel"},
  {"@type": "ProductGroup", "name": "Sessel", "hasVariant": [{
    "@type": "Product", "name": "Sessel Ohrensessel &amp; Hocker",
    "image": {"@type": "ImageObject", "url": "/bilder/sessel.png"},
    "aggregateRating": {"ratingValue": "9", "bestRating": "10", "reviewCount": 87},
    "offers": [{"@type": "Offer", "price": "349.00", "priceCurrency": "EUR",
                "availability": "https://schema.org/LimitedAvailability"}]
  }]}
]}</script>
</head><body><h1>Sessel</h1></body></html>"""

OPEN_GRAPH_PAGE = """<html><head>
<meta property="og:title" content="Lampe Bogen">
<meta property="og:image" content="https://cdn.shop.example.com/lampe.jpg">
<meta property="product:price:amount" content="89,90">
<meta property="product:price:currency" content="CHF">
</head></html>"""


def png_bytes(size=(900, 600)):
    output = io.BytesIO()
    Image.new("RGBA", size, (200, 30, 30, 128)).save(output, "PNG")
    return output.getvalue()


class WishlistParsingTests(SimpleTestCase):
    def test_reads_title_price_picture_rating_and_availability(self):
        product = parse_product_page(PRODUCT_PAGE)

        self.assertEqual(product["title"], "Kaffeemaschine & Mühle, Edelstahl")
        self.assertEqual(product["price"], Decimal("1299.99"))
        self.assertEqual(product["currency"], "€")
        self.assertEqual(product["image_url"], "https://m.media-amazon.com/images/I/71abc._AC_SL400_.jpg")
        self.assertEqual(product["rating"], Decimal("4.6"))
        self.assertEqual(product["review_count"], 1234)
        self.assertEqual(product["availability"], "Auf Lager")
        self.assertEqual(product["canonical"], "https://www.amazon.de/kaffeemaschine/dp/B0TEST1234")

    def test_amazon_falls_back_to_the_price_label(self):
        page = (
            '<span id="productTitle">Lampe</span>'
            '<span id="apex-pricetopay-accessibility-label" class="aok-offscreen"> 24,95&nbsp;&euro; </span>'
        )

        product = parse_product_page(page)

        self.assertEqual(product["price"], Decimal("24.95"))
        self.assertEqual(product["image_url"], "")
        self.assertIsNone(product["rating"])

    def test_other_shops_are_read_from_their_json_ld_product(self):
        product = parse_product_page(SHOP_PAGE, "https://www.moebel.example.com/p/sessel?farbe=blau")

        self.assertEqual(product["title"], "Sessel Ohrensessel & Hocker")
        self.assertEqual(product["price"], Decimal("349.00"))
        self.assertEqual(product["currency"], "€")
        self.assertEqual(product["image_url"], "https://www.moebel.example.com/bilder/sessel.png")
        self.assertEqual(product["rating"], Decimal("4.5"))
        self.assertEqual(product["review_count"], 87)
        self.assertEqual(product["availability"], "Nur noch wenige")

    def test_open_graph_tags_stand_in_for_missing_json_ld(self):
        product = parse_product_page(OPEN_GRAPH_PAGE, "https://shop.example.com/lampe")

        self.assertEqual(product["title"], "Lampe Bogen")
        self.assertEqual(product["price"], Decimal("89.90"))
        self.assertEqual(product["currency"], "CHF")
        self.assertEqual(product["image_url"], "https://cdn.shop.example.com/lampe.jpg")
        self.assertIsNone(product["rating"])

    def test_page_without_product_data_is_no_product(self):
        self.assertIsNone(parse_product_page("<html><title>Robot Check</title></html>"))
        self.assertIsNone(
            parse_product_page('<script type="application/ld+json">{"@type": "Product"</script>')
        )

    def test_amounts_in_german_and_english_notation(self):
        for text, expected in [
            ("1.299,99", "1299.99"),
            ("1,299.99", "1299.99"),
            ("39.99", "39.99"),
            ("39,9", "39.90"),
            ("1.299", "1299.00"),
            ("1299", "1299.00"),
        ]:
            with self.subTest(text=text):
                self.assertEqual(_amount(text), Decimal(expected))
        self.assertIsNone(_amount("abc"))

    def test_product_links_are_cut_down_to_the_asin(self):
        url = "https://www.amazon.de/Kaffee-Muehle/dp/b0test1234/ref=sr_1_1?tag=partner-21&th=1"

        self.assertEqual(product_url(url), ("https://www.amazon.de/dp/B0TEST1234", "B0TEST1234"))
        self.assertEqual(product_url("https://amzn.eu/d/abc123"), ("https://amzn.eu/d/abc123", ""))
        self.assertEqual(title_from_url(url), "Kaffee Muehle")
        self.assertEqual(title_from_url("https://www.amazon.de/dp/B0TEST1234"), "Amazon-Artikel")

    def test_other_shop_links_lose_only_their_tracking_parameters(self):
        url = (
            "https://www.otto.de/p/philips-airfryer-xxl-S0F2Z0EN/?variationId=42&utm_source=x&gclid=y#reviews"
        )

        self.assertEqual(
            product_url(url), ("https://www.otto.de/p/philips-airfryer-xxl-S0F2Z0EN/?variationId=42", "")
        )
        self.assertEqual(title_from_url(url), "philips airfryer xxl S0F2Z0EN")
        self.assertEqual(
            title_from_url("https://www.shop.example.com/p/12345"), "Artikel von shop.example.com"
        )

    def test_amazon_pages_without_product_and_internal_addresses_are_refused(self):
        for url in [
            "https://www.amazon.de/gp/help/customer",
            "http://127.0.0.1/dp/B0TEST1234",
            "http://intranet/produkt",
        ]:
            with self.subTest(url=url), self.assertRaises(FetchError):
                product_url(url)

    def test_totals_count_quantities_per_currency(self):
        items = [
            WishListItem(price=Decimal("10.50"), quantity=2, currency="€"),
            WishListItem(price=None, quantity=1, currency="€"),
            WishListItem(price=Decimal("1000"), quantity=1, currency="€"),
            WishListItem(price=Decimal("5"), quantity=1, currency="$"),
        ]

        self.assertEqual(list_totals(items), ["1.021,00 €", "5,00 $"])


class WishlistViewTests(TestCase):
    def setUp(self):
        cache.clear()
        self.lea = User.objects.create_user(username="lea", email="lea@example.com", password="pw-12345!")
        self.client.force_login(self.lea)
        self.wish_list = WishList.objects.create(owner=self.lea, title="Geburtstag")
        self.url = f"/tools/wishlist/{self.wish_list.pk}/"
        FakeHttpConnection.responses = []
        FakeHttpConnection.requests = []
        patcher = patch.multiple(
            "app.services.safe_fetch.http.client",
            HTTPSConnection=FakeHttpConnection,
            HTTPConnection=FakeHttpConnection,
        )
        patcher.start()
        self.addCleanup(patcher.stop)

    def _add(self, url="https://www.amazon.de/Kaffee/dp/B0TEST1234?tag=partner-21"):
        return self.client.post(self.url, {"form_name": "wish_item_add", "url": url}, follow=True)

    def test_adding_a_link_stores_the_product_and_shows_the_total(self):
        FakeHttpConnection.responses = [(200, {}, PRODUCT_PAGE.encode())]

        response = self._add()

        item = WishListItem.objects.get(wish_list=self.wish_list)
        self.assertEqual(FakeHttpConnection.requests, [("www.amazon.de", "/dp/B0TEST1234")])
        self.assertEqual(item.title, "Kaffeemaschine & Mühle, Edelstahl")
        self.assertEqual(item.url, "https://www.amazon.de/dp/B0TEST1234")
        self.assertEqual(item.price, Decimal("1299.99"))
        self.assertIsNotNone(item.fetched_at)
        self.assertContains(response, "Gesamt: <strong>1.299,99 €</strong>", html=False)
        self.assertContains(response, "1.234 Bewertungen")

    def test_short_links_take_the_asin_from_the_page_but_product_links_keep_theirs(self):
        FakeHttpConnection.responses = [
            (301, {"Location": "https://www.amazon.de/dp/B0TEST1234?tag=x"}, b""),
            (200, {}, PRODUCT_PAGE.encode()),
            (200, {}, PRODUCT_PAGE.encode()),
        ]

        self._add("https://amzn.eu/d/abc123")
        self._add("https://www.amazon.de/dp/B0OLD00001")

        self.assertEqual(
            list(WishListItem.objects.values_list("asin", "url")),
            [
                ("B0TEST1234", "https://www.amazon.de/dp/B0TEST1234"),
                ("B0OLD00001", "https://www.amazon.de/dp/B0OLD00001"),
            ],
        )

    def test_blocked_lookup_keeps_the_item_for_manual_details(self):
        FakeHttpConnection.responses = [(503, {}, b"")]

        response = self._add()

        item = WishListItem.objects.get(wish_list=self.wish_list)
        self.assertEqual(item.title, "Kaffee")
        self.assertIsNone(item.price)
        self.assertContains(response, "trag Titel und Preis selbst ein")
        self.assertContains(response, "Preis fehlt")

    def test_manual_price_with_comma_and_quantity_count_into_the_total(self):
        item = WishListItem.objects.create(
            wish_list=self.wish_list, url="https://www.amazon.de/dp/B0TEST1234", title="X"
        )

        response = self.client.post(
            self.url,
            {
                "form_name": "wish_item_update",
                "item_id": item.pk,
                "title": "Tasse",
                "price": "4,50",
                "quantity": "3",
            },
            follow=True,
        )

        item.refresh_from_db()
        self.assertEqual((item.title, item.price, item.quantity), ("Tasse", Decimal("4.50"), 3))
        self.assertContains(response, "Gesamt: <strong>13,50 €</strong>", html=False)

    def test_refresh_updates_the_price(self):
        item = WishListItem.objects.create(
            wish_list=self.wish_list, url="https://www.amazon.de/dp/B0TEST1234", title="X", price=Decimal("1")
        )
        FakeHttpConnection.responses = [(200, {}, PRODUCT_PAGE.encode())]

        self.client.post(self.url, {"form_name": "wish_item_refresh", "item_id": item.pk})

        item.refresh_from_db()
        self.assertEqual(item.price, Decimal("1299.99"))

    def test_same_product_twice_is_refused_without_a_request(self):
        WishListItem.objects.create(
            wish_list=self.wish_list, url="https://www.amazon.de/dp/B0TEST1234", asin="B0TEST1234", title="X"
        )
        WishListItem.objects.create(
            wish_list=self.wish_list, url="https://shop.example.com/p/sessel", title="Y"
        )

        self.assertContains(self._add(), "steht schon auf der Liste")
        self.assertContains(
            self._add("https://shop.example.com/p/sessel?utm_source=mail"), "steht schon auf der Liste"
        )
        self.assertEqual(FakeHttpConnection.requests, [])
        self.assertEqual(WishListItem.objects.count(), 2)

    def test_adding_a_link_from_another_shop(self):
        FakeHttpConnection.responses = [(200, {}, SHOP_PAGE.encode())]

        self._add("https://www.moebel.example.com/p/sessel?farbe=blau&utm_campaign=herbst")

        item = WishListItem.objects.get(wish_list=self.wish_list)
        self.assertEqual(FakeHttpConnection.requests, [("www.moebel.example.com", "/p/sessel?farbe=blau")])
        self.assertEqual(
            (item.title, item.price, item.asin), ("Sessel Ohrensessel & Hocker", Decimal("349.00"), "")
        )
        self.assertEqual(item.image_url, "https://www.moebel.example.com/bilder/sessel.png")

    def test_pictures_are_served_re_encoded_and_cached(self):
        item = WishListItem.objects.create(
            wish_list=self.wish_list,
            url="https://shop.example.com/p/sessel",
            title="Sessel",
            image_url="https://cdn.shop.example.com/sessel.png",
        )
        FakeHttpConnection.responses = [(200, {}, png_bytes())]

        first = self.client.get(f"/tools/wishlist/image/{item.pk}/")
        second = self.client.get(f"/tools/wishlist/image/{item.pk}/")

        self.assertEqual(first.status_code, 200)
        self.assertEqual(first["Content-Type"], "image/jpeg")
        self.assertIn("private", first["Cache-Control"])
        with Image.open(io.BytesIO(first.content)) as picture:
            self.assertEqual((picture.format, picture.size), ("JPEG", (400, 267)))
        self.assertEqual(second.content, first.content)
        self.assertEqual(FakeHttpConnection.requests, [("cdn.shop.example.com", "/sessel.png")])

    def test_pictures_that_are_no_plain_images_and_foreign_items_are_not_served(self):
        item = WishListItem.objects.create(
            wish_list=self.wish_list,
            url="https://shop.example.com/p/sessel",
            title="Sessel",
            image_url="https://cdn.shop.example.com/sessel.svg",
        )
        other = User.objects.create_user(username="max", email="max@example.com", password="pw-12345!")
        foreign = WishListItem.objects.create(
            wish_list=WishList.objects.create(owner=other, title="Privat"),
            url="https://shop.example.com/p/lampe",
            title="Lampe",
            image_url="https://cdn.shop.example.com/lampe.png",
        )
        FakeHttpConnection.responses = [(200, {}, b'<svg xmlns="http://www.w3.org/2000/svg"><script/></svg>')]

        self.assertEqual(self.client.get(f"/tools/wishlist/image/{item.pk}/").status_code, 404)
        self.assertEqual(self.client.get(f"/tools/wishlist/image/{foreign.pk}/").status_code, 404)
        self.assertEqual(len(FakeHttpConnection.requests), 1)

    def test_lookups_are_throttled_per_user(self):
        FakeHttpConnection.responses = [(503, {}, b"")] * 30

        for number in range(31):
            self._add(f"https://www.amazon.de/dp/B0TEST{number:04d}")

        self.assertEqual(len(FakeHttpConnection.requests), 30)
        self.assertEqual(WishListItem.objects.count(), 31)

    def test_lists_of_others_are_not_found(self):
        other = User.objects.create_user(username="max", email="max@example.com", password="pw-12345!")
        foreign = WishList.objects.create(owner=other, title="Privat")

        self.assertEqual(self.client.get(f"/tools/wishlist/{foreign.pk}/").status_code, 404)
        self.assertNotContains(self.client.get("/tools/wishlist/"), "Privat")

    def test_create_rename_and_delete_a_list(self):
        response = self.client.post(
            "/tools/wishlist/", {"form_name": "wish_list_create", "title": "Weihnachten"}
        )
        created = WishList.objects.get(title="Weihnachten")
        self.assertRedirects(response, f"/tools/wishlist/{created.pk}/")

        self.client.post(f"/tools/wishlist/{created.pk}/", {"form_name": "wish_list_rename", "title": "Xmas"})
        created.refresh_from_db()
        self.assertEqual(created.title, "Xmas")

        response = self.client.post(f"/tools/wishlist/{created.pk}/", {"form_name": "wish_list_delete"})
        self.assertRedirects(response, "/tools/wishlist/")
        self.assertFalse(WishList.objects.filter(pk=created.pk).exists())

    def test_tools_hub_links_the_wish_lists(self):
        self.assertContains(self.client.get("/tools/"), "/tools/wishlist/")
