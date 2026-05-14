"""
Reimo-specifieke configuratie & fallback parser.

Reimo Reisemobil-Center GmbH (DE) - hoofdleverancier camper-onderdelen.
BTW: DE113580270, IBAN: DE49506521240026001933.
Intracommunautaire levering 0% BTW (verlegging).
"""

REIMO_CONFIG = {
    "leverancier_naam": "Reimo Reisemobil-Center",
    "vat": "DE113580270",
    "land": "DE",
    "default_btw": 0,  # Intracom verlegging
    "default_btw_tag": "Intracommunautaire verwerving 21%",
    "is_storable_default": True,  # Reimo levert echte goederen, voorraad-product
    "default_account_in_bill": "604000 Handelsgoederen",
    "naming_keywords": ["Reimo", "Reisemobil", "Camper"],
    # Reimo factuurkenmerken: artikelnummer-prefix [12345] of [398192]
    "sku_regex": r"\[(\d{5,6})\]",
}


# Voor toekomstige uitbreiding: standaard product-mapping voor Reimo
# Indien een Reimo-product NIET gematched wordt, gebruik dan deze hints
COMMON_REIMO_PRODUCTS = {
    # SKU prefix → typische producttype
    "31": "Klapraam / Window",
    "39": "Maxxair dakluik / dome",
    "59": "Zwenkconsole / Swivel",
    "72": "Truma heater",
    "27": "Watertank / pomp",
}
