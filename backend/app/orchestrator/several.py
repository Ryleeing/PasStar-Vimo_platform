"""Spotting a message that asks for several products at once ("200 L milk and 20 L cream").

One request is one product for now; ordering several in one message comes in a later version. Until then such a
message is answered kindly (on the screens and to agents) instead of being half-understood: Aimo lists what it
heard and asks which product to start with.

A message counts as several products only when it splits into at least two parts (joined by "and", "ja", "och",
a comma, a semicolon, "&", "+" or a new line) that each name a product, so "milk for the cream sauce" is still one.
"""
from __future__ import annotations

import re

# label -> words (en / fi / sv). Longer phrases are matched first, so "ice cream" is not also "cream".
PRODUCTS: dict[str, list[str]] = {
    "ice cream": ["ice cream", "ice-cream", "jäätelö"],
    "cream cheese": ["cream cheese", "tuorejuusto", "färskost"],
    "milk powder": ["milk powder", "maitojauhe", "mjölkpulver"],
    "milk": ["milk", "maito", "mjölk"],
    "cream": ["cream", "kerma", "grädde"],
    "quark": ["quark", "rahka", "kvarg"],
    "cheese": ["cheese", "juusto", "ost"],
    "butter": ["butter", "voi", "smör"],
    "yoghurt": ["yoghurt", "yogurt", "jogurtti", "yoghurt"],
    "eggs": ["egg", "eggs", "kananmuna", "munat", "ägg"],
    "pasta": ["pasta", "spaghetti", "macaroni", "makaroni"],
    "rice": ["rice", "riisi", "ris"],
    "flour": ["flour", "jauho", "mjöl"],
    "chicken": ["chicken", "kana", "broiler", "kyckling"],
    "fish": ["fish", "salmon", "lohi", "kala", "fisk", "lax"],
    "bread": ["bread", "leipä", "bröd", "buns", "baguette"],
    "coffee": ["coffee", "kahvi", "kaffe"],
    "juice": ["juice", "mehu"],
    "oil": ["oil", "öljy", "olja"],
    "sugar": ["sugar", "sokeri", "socker"],
    "meat": ["meat", "beef", "pork", "lamb", "veal", "mince", "minced", "sausage", "ham", "bacon", "turkey",
             "liha", "naudan", "possu", "sianliha", "jauheliha", "makkara", "kinkku", "kött", "nötkött", "fläsk", "korv"],
    "vegetables": ["vegetable", "vegetables", "veggies", "salad", "lettuce", "tomato", "tomatoes", "onion", "onions",
                   "carrot", "carrots", "vihannes", "vihannekset", "tomaatti", "sipuli", "porkkana", "grönsaker", "lök"],
    "potatoes": ["potato", "potatoes", "peruna", "potatis"],
    "fruit": ["fruit", "apple", "apples", "banana", "bananas", "berries", "hedelmä", "omena", "banaani", "frukt", "äpple"],
}
_TERMS = sorted(((w, label) for label, ws in PRODUCTS.items() for w in ws), key=lambda t: -len(t[0]))
_SPLIT = re.compile(r"\s*(?:,|;|&|\+|\n|\band\b|\bja\b|\boch\b|\bplus\b)\s*", re.I)


def _product_in(part: str) -> str | None:
    low = part.lower()
    for word, label in _TERMS:
        # Short words must stand alone (a plural "s" allowed), so "voi" is not "voitko" and "ost" not "most";
        # longer ones may carry an ending, as Finnish does ("maitoa", "kermaa").
        end = r"s?(?![\wåäö])" if len(word) <= 3 else ""
        if re.search(rf"(?<![\wåäö]){re.escape(word)}{end}", low):
            return label
    return None


def several_products(text: str) -> list[dict[str, str]]:
    """The products of a several-product message, each with the words that asked for it; [] for one product."""
    items: list[dict[str, str]] = []
    for part in (p.strip(" .!?") for p in _SPLIT.split(text or "")):
        label = part and _product_in(part)
        if label and label not in {i["product"] for i in items}:
            items.append({"product": label, "text": part})
    return items if len(items) > 1 else []
