"""Check the title-level signals present in the three frozen anonymous storefronts."""
import json
import re
from html.parser import HTMLParser


class Page(HTMLParser):
    def __init__(self, html):
        super().__init__()
        self.script = self.button = None
        self.scripts = []
        self.buttons = {}
        self.canonicals = []
        self.nl_chart = False
        self.feed(html)
        self.close()

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "script":
            self.script = attrs
        elif tag == "button":
            self.button = attrs.get("data-testid")
            if self.button:
                self.buttons.setdefault(self.button, []).append([])
        elif tag == "link" and attrs.get("rel") == "canonical":
            self.canonicals.append(attrs.get("href"))

    def handle_endtag(self, tag):
        if tag == "script":
            self.script = None
        elif tag == "button":
            self.button = None

    def handle_data(self, data):
        if self.script is not None:
            self.scripts.append((self.script, data))
        elif self.button:
            self.buttons[self.button][-1].append(data)
        elif "Top 10 films in Nederland vandaag" in data:
            self.nl_chart = True

    def structured(self, kind):
        entries = (json.loads(data) for attrs, data in self.scripts
                   if attrs.get("type") == "application/ld+json")
        entry, = (item for item in entries if item.get("@type") == kind)
        return entry


def netflix(page, row):
    kind = {"movie": "Movie", "series": "TVSeries"}[row["media_type"]]
    entry = page.structured(kind)
    marker = "netflix.reactContext ="
    context, = (data.partition(marker)[2].lstrip() for _, data in page.scripts if marker in data)
    # Decode the captured JS string escapes as JSON without executing page code.
    models = json.JSONDecoder().raw_decode(re.sub(r"\\x([0-9A-Fa-f]{2})", r"\\u00\1", context))[0]["models"]
    title = models["nmTitleGQL"]["data"]
    market = models["adtech"]["data"]["iframeData"]
    return (row["offer_class"] == "subscription" and entry["url"] == row["public_url"]
            and entry["name"] == row["page_title"] and market["membership_status"] == "ANONYMOUS"
            and market["country"] == row["country"] and title["isPlayableOnAdsPlan"] is True
            and title["metaData"]["isAvailable"] is True
            and str(title["metaData"]["topLevelVideoId"]) == row["provider_title_id"])


def prime(page, row):
    request, = (json.loads(data)["RequestContext"] for attrs, data in page.scripts
                if attrs.get("id") == "dv-web-global-store-data")
    states = (json.loads(data) for _, data in page.scripts if data.lstrip().startswith("{"))
    body, = (state["init"]["preparations"]["body"] for state in states if "init" in state)
    atf = body["atf"]["state"]
    title_id = atf["pageTitleId"]
    title = body["btf"]["state"]["detail"]["detail"][title_id]
    if not (request["currentTerritory"] == request["recordTerritory"] == row["country"]
            and title_id == row["provider_title_id"] and title["title"] == row["page_title"]
            and title["titleType"] == row["media_type"] and str(title["releaseYear"]) == row["canonical_year"]):
        return False
    actions = atf["action"]["atf"][title_id]["primaryActions"]
    if row["offer_class"] == "addon":
        options = (option for action in actions for option in action.get("payload", {}).get("cardOptions", []))
        metadata = (json.loads(action["actionMetadata"]) for option in options for action in option["actions"])
        return row["canonical_provider"] == "max_amazon" and any(
            (m["benefitId"], m["subscriptionType"]) == ("wbdmaxnl", "PV_CHANNEL") for m in metadata)
    metadata = [json.loads(action["actionMetadata"]) for action in actions]
    if row["canonical_provider"] != "prime":
        return False
    if row["offer_class"] == "subscription":
        return title["isPrime"] is True and any(
            (m["benefitId"], m["subscriptionType"]) == ("Prime", "PRIME") for m in metadata)
    transaction = {"rent": "RENTAL", "buy": "PURCHASE"}[row["offer_class"]]
    return any(m["transactionType"] == transaction and m["label"].endswith(" €" + row["price_eur"])
               for m in metadata)


def rakuten(page, row):
    movie = page.structured("Movie")
    button, = page.buttons[row["offer_class"] + "-details-button"]
    url = "https://www.rakuten.tv/nl/movies/" + row["provider_title_id"]
    return (page.canonicals == [url] and url == row["public_url"] and page.nl_chart and row["country"] == "NL"
            and row["media_type"] == "movie" and movie["name"] == row["page_title"]
            and str(movie["copyrightYear"]) == row["canonical_year"]
            and re.findall(r"€\s*(\d+,\d{2})", "".join(button)) == [row["price_eur"].replace(".", ",")])


def verify_page(page, row):
    parsers = {"netflix": netflix, "prime": prime, "max_amazon": prime, "rakuten_tv": rakuten}
    if not parsers[row["canonical_provider"]](page, row):
        raise ValueError(f"Frozen title/market/offer signals differ for {row['id']}")
