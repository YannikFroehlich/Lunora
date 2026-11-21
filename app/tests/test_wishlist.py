import io
from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth.models import User
from django.core.cache import cache
from django.test import Client, SimpleTestCase, TestCase
from django.utils import timezone
from PIL import Image

from app.models import (
    UserNotification,
    WishList,
    WishListItem,
    WishListItemPricePoint,
    WishListReservation,
    WishListShare,
)
from app.services.notes import display_name
from app.services.safe_fetch import FetchError
from app.services.wishlist import (
    _amount,
    list_totals,
    parse_product_page,
    price_change,
    product_url,
    refresh_due_prices,
    sparkline_points,
    title_from_url,
)
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

    def test_price_change_compares_against_the_oldest_recorded_price(self):
        item = WishListItem(price=Decimal("100"), currency="€")
        no_history = []
        self.assertEqual(price_change(item, no_history), ("", False))

        same_price = [WishListItemPricePoint(price=Decimal("100"))]
        self.assertEqual(price_change(item, same_price), ("", False))

        got_cheaper = [WishListItemPricePoint(price=Decimal("130"))]
        self.assertEqual(price_change(item, got_cheaper), ("-30,00 €", False))

        got_pricier = [WishListItemPricePoint(price=Decimal("80"))]
        self.assertEqual(price_change(item, got_pricier), ("+20,00 €", True))

    def test_totals_count_quantities_per_currency(self):
        items = [
            WishListItem(price=Decimal("10.50"), quantity=2, currency="€"),
            WishListItem(price=None, quantity=1, currency="€"),
            WishListItem(price=Decimal("1000"), quantity=1, currency="€"),
            WishListItem(price=Decimal("5"), quantity=1, currency="$"),
        ]

        self.assertEqual(list_totals(items), ["1.021,00 €", "5,00 $"])

    def test_sparkline_puts_the_cheapest_price_at_the_bottom(self):
        def points(*prices):
            return [WishListItemPricePoint(price=Decimal(price)) for price in prices]

        self.assertEqual(sparkline_points(points("10", "20", "15")), "0.0,22.0 40.0,2.0 80.0,12.0")
        self.assertEqual(sparkline_points(points("5", "5")), "0.0,12.0 80.0,12.0")
        self.assertEqual(sparkline_points(points("5")), "")


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

    def test_refresh_updates_the_price_and_records_price_history(self):
        item = WishListItem.objects.create(
            wish_list=self.wish_list, url="https://www.amazon.de/dp/B0TEST1234", title="X", price=Decimal("1")
        )
        WishListItemPricePoint.objects.create(item=item, price=Decimal("1"))
        FakeHttpConnection.responses = [(200, {}, PRODUCT_PAGE.encode())]

        response = self.client.post(
            self.url, {"form_name": "wish_item_refresh", "item_id": item.pk}, follow=True
        )

        item.refresh_from_db()
        self.assertEqual(item.price, Decimal("1299.99"))
        self.assertEqual(
            list(item.price_points.values_list("price", flat=True)), [Decimal("1"), Decimal("1299.99")]
        )
        self.assertContains(response, "+1.298,99 €")
        self.assertContains(response, "seit Hinzufügen")

    def test_refresh_all_updates_every_item_and_reports_the_count(self):
        first = WishListItem.objects.create(
            wish_list=self.wish_list, url="https://www.amazon.de/dp/B0TEST1234", title="X", price=Decimal("1")
        )
        second = WishListItem.objects.create(
            wish_list=self.wish_list, url="https://shop.example.com/p/sessel", title="Y", price=Decimal("1")
        )
        FakeHttpConnection.responses = [(200, {}, PRODUCT_PAGE.encode()), (200, {}, SHOP_PAGE.encode())]

        response = self.client.post(self.url, {"form_name": "wish_list_refresh_all"}, follow=True)

        first.refresh_from_db()
        second.refresh_from_db()
        self.assertEqual(first.price, Decimal("1299.99"))
        self.assertEqual(second.price, Decimal("349.00"))
        self.assertContains(response, "2 Preis(e) aktualisiert.")

    def test_quantity_stepper_changes_quantity_without_the_edit_form(self):
        item = WishListItem.objects.create(
            wish_list=self.wish_list, url="https://www.amazon.de/dp/B0TEST1234", title="X", quantity=1
        )

        self.client.post(self.url, {"form_name": "wish_item_quantity", "item_id": item.pk, "delta": "up"})
        item.refresh_from_db()
        self.assertEqual(item.quantity, 2)

        self.client.post(self.url, {"form_name": "wish_item_quantity", "item_id": item.pk, "delta": "down"})
        self.client.post(self.url, {"form_name": "wish_item_quantity", "item_id": item.pk, "delta": "down"})
        item.refresh_from_db()
        self.assertEqual(item.quantity, 0)

        self.client.post(self.url, {"form_name": "wish_item_quantity", "item_id": item.pk, "delta": "down"})
        item.refresh_from_db()
        self.assertEqual(item.quantity, 0)

    def test_quantity_zero_keeps_the_item_and_counts_as_nothing_in_the_total(self):
        item = WishListItem.objects.create(
            wish_list=self.wish_list,
            url="https://www.amazon.de/dp/B0TEST1234",
            title="X",
            price=Decimal("49.99"),
            quantity=0,
        )

        response = self.client.get(self.url)

        self.assertTrue(WishListItem.objects.filter(pk=item.pk).exists())
        self.assertContains(response, "Gesamt: <strong>0,00 €</strong>", html=False)

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

    def _item(self, **fields):
        defaults = {
            "wish_list": self.wish_list,
            "url": "https://www.amazon.de/dp/B0TEST1234",
            "title": "Kaffee",
        }
        return WishListItem.objects.create(**{**defaults, **fields})

    def test_priority_and_target_price_sort_and_badge_the_items(self):
        later = self._item(title="Tasse", price=Decimal("8"), url="https://shop.example.com/tasse")
        self._item(title="Mühle", price=Decimal("50"), url="https://shop.example.com/muehle")

        response = self.client.post(
            self.url,
            {
                "form_name": "wish_item_update",
                "item_id": later.pk,
                "title": "Tasse",
                "price": "8",
                "quantity": "2",
                "priority": "high",
                "target_price": "9,50",
            },
            follow=True,
        )

        later.refresh_from_db()
        self.assertEqual((later.priority, later.target_price), ("high", Decimal("9.50")))
        self.assertEqual([item.title for item in response.context["items"]], ["Tasse", "Mühle"])
        self.assertContains(response, "davon „Muss haben“: 16,00 €")
        self.assertContains(response, "Zielpreis erreicht (9,50 €)")

    def test_scheduled_price_check_raises_one_alert_when_the_target_is_reached(self):
        item = self._item(price=Decimal("50"), target_price=Decimal("40"))
        WishListItemPricePoint.objects.create(item=item, price=Decimal("50"))
        self._item(price=Decimal("10"), url="https://shop.example.com/ohne-alarm")
        FakeHttpConnection.responses = [(200, {}, PRODUCT_PAGE.replace("1299.99", "39.99").encode())]
        now = timezone.now()

        self.assertEqual(refresh_due_prices(now=now), {"checked": 1, "failed": 0})
        # Within the interval nothing is fetched again, and items without a target are never fetched.
        self.assertEqual(refresh_due_prices(now=now), {"checked": 0, "failed": 0})

        item.refresh_from_db()
        self.assertEqual(item.price, Decimal("39.99"))
        self.assertEqual(len(FakeHttpConnection.requests), 1)
        alert = UserNotification.objects.get(recipient=self.lea, kind=UserNotification.KIND_WISH_PRICE_ALERT)
        self.assertIn("39,99 €", alert.body)
        self.assertIn("40,00 €", alert.body)
        self.assertEqual(alert.url, f"/tools/wishlist/{self.wish_list.pk}/#wish-{item.pk}")

        # Cheaper still, but already below the target: no second alert.
        FakeHttpConnection.responses = [(200, {}, PRODUCT_PAGE.replace("1299.99", "35.00").encode())]
        refresh_due_prices(now=now + timedelta(hours=13))
        item.refresh_from_db()
        self.assertEqual(item.price, Decimal("35.00"))
        self.assertEqual(
            UserNotification.objects.filter(kind=UserNotification.KIND_WISH_PRICE_ALERT).count(), 1
        )

    def test_failed_price_check_waits_for_the_next_interval(self):
        item = self._item(price=Decimal("50"), target_price=Decimal("40"))
        FakeHttpConnection.responses = [(503, {}, b"")]

        self.assertEqual(refresh_due_prices(now=timezone.now()), {"checked": 0, "failed": 1})

        item.refresh_from_db()
        self.assertIsNotNone(item.price_checked_at)
        self.assertEqual(refresh_due_prices(now=timezone.now()), {"checked": 0, "failed": 0})

    def test_shared_list_lets_members_reserve_without_the_owner_seeing_it(self):
        tom = User.objects.create_user(username="tom", email="tom@example.com", password="pw-12345!")
        anna = User.objects.create_user(username="anna", email="anna@example.com", password="pw-12345!")
        gift = self._item(title="Kaffeemühle", price=Decimal("50"))
        self._item(title="Pausiert", url="https://shop.example.com/pause", quantity=0)
        for identifier in ("TOM@example.com", "anna"):
            self.client.post(self.url, {"form_name": "wish_list_share", "identifier": identifier})
        self.assertEqual(WishListShare.objects.filter(wish_list=self.wish_list).count(), 2)
        self.assertTrue(
            UserNotification.objects.filter(
                recipient=tom, kind=UserNotification.KIND_WISH_LIST_SHARE
            ).exists()
        )

        tom_client, anna_client = Client(), Client()
        tom_client.force_login(tom)
        anna_client.force_login(anna)
        self.assertContains(tom_client.get("/tools/wishlist/"), "Mit dir geteilt")
        page = tom_client.get(self.url)
        self.assertContains(page, "Ich schenke das")
        self.assertNotContains(page, "Pausiert")
        self.assertNotContains(page, "Bearbeiten")

        tom_client.post(self.url, {"form_name": "wish_item_reserve", "item_id": gift.pk})
        self.assertEqual(WishListReservation.objects.get(item=gift).user, tom)
        self.assertContains(tom_client.get(self.url), "Du schenkst das")
        self.assertContains(anna_client.get(self.url), f"{display_name(tom)} schenkt das")
        anna_client.post(self.url, {"form_name": "wish_item_reserve", "item_id": gift.pk})
        self.assertEqual(WishListReservation.objects.get(item=gift).user, tom)

        owner_page = self.client.get(self.url)
        self.assertNotContains(owner_page, "schenkt das")
        self.assertNotContains(owner_page, "Du schenkst")
        self.assertFalse(hasattr(owner_page.context["items"][0], "reserved_by"))

        # Members can not change the list.
        tom_client.post(self.url, {"form_name": "wish_item_delete", "item_id": gift.pk})
        tom_client.post(self.url, {"form_name": "wish_list_share", "identifier": "lea"})
        self.assertTrue(WishListItem.objects.filter(pk=gift.pk).exists())
        self.assertEqual(WishListShare.objects.filter(wish_list=self.wish_list).count(), 2)

        response = tom_client.post(self.url, {"form_name": "wish_list_leave"})
        self.assertRedirects(response, "/tools/wishlist/")
        self.assertFalse(WishListReservation.objects.exists())
        self.assertFalse(
            UserNotification.objects.filter(
                recipient=tom, kind=UserNotification.KIND_WISH_LIST_SHARE
            ).exists()
        )
        self.assertEqual(tom_client.get(self.url).status_code, 404)

    def test_members_get_the_pictures_and_strangers_do_not(self):
        tom = User.objects.create_user(username="tom", email="tom@example.com", password="pw-12345!")
        stranger = User.objects.create_user(username="eve", email="eve@example.com", password="pw-12345!")
        item = self._item(image_url="https://m.media-amazon.com/images/I/71abc.jpg")
        WishListShare.objects.create(wish_list=self.wish_list, user=tom)
        FakeHttpConnection.responses = [(200, {"Content-Type": "image/png"}, png_bytes())]

        member, outsider = Client(), Client()
        member.force_login(tom)
        outsider.force_login(stranger)
        self.assertEqual(member.get(f"/tools/wishlist/image/{item.pk}/").status_code, 200)
        self.assertEqual(outsider.get(f"/tools/wishlist/image/{item.pk}/").status_code, 404)
