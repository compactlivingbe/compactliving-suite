"""Leverancier-adapters.

Elke leverancier implementeert dezelfde interface (zie base.Supplier) en levert
genormaliseerde producten. Zo werken diff, import, dashboard en
inkoop-optimalisatie met één gemeenschappelijke vorm, en is een nieuwe
leverancier toevoegen niets meer dan een nieuw adapter-bestand registreren.
"""
from .base import Supplier, SupplierProduct, register, get, all_suppliers  # noqa: F401

# Adapters importeren = registreren (side effect). Nieuwe leverancier? Voeg een
# import toe en de rest van de app pikt 'm automatisch op.
from . import allspark  # noqa: F401,E402
from . import topsystems  # noqa: F401,E402
