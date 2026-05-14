"""PDF → gestructureerde data via Claude API (multimodal, leest PDF direct)."""
import os
import base64
import json
from anthropic import Anthropic

EXTRACT_SCHEMA = """{
  "leverancier": {
    "naam": "string - bedrijfsnaam exact zoals op factuur",
    "vat": "string of null - BTW-nummer (bv. DE113580270, BE0727689347)",
    "land": "string - land code (BE/DE/NL/...)"
  },
  "factuur": {
    "nummer": "string - factuurnummer/referentie",
    "datum": "string - YYYY-MM-DD",
    "valuta": "EUR (default)",
    "totaal_excl_btw": "number - totaal exclusief BTW",
    "totaal_btw": "number - totale BTW",
    "totaal_incl_btw": "number"
  },
  "lijnen": [
    {
      "beschrijving": "string - exacte beschrijving van product",
      "artikelnummer": "string of null - SKU/artikelnummer indien aanwezig",
      "hoeveelheid": "number",
      "eenheidsprijs_excl_btw": "number",
      "totaal_excl_btw": "number - hoeveelheid × eenheidsprijs",
      "btw_percentage": "number - 0, 6, 12, 21 (Belgisch tarief) of leveranciersland tarief",
      "is_dienst": "boolean - true voor diensten, false voor goederen"
    }
  ]
}"""


def pdf_to_base64(pdf_path: str) -> str:
    with open(pdf_path, "rb") as f:
        return base64.standard_b64encode(f.read()).decode("utf-8")


def extract_from_pdf(pdf_path: str, leverancier_hint: str = None, model: str = "claude-sonnet-4-6") -> dict:
    """Stuur PDF naar Claude API en krijg gestructureerde factuurdata terug."""
    client = Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])

    hint_text = ""
    if leverancier_hint:
        hint_text = f"\n\nLeverancier-hint: deze factuur is van **{leverancier_hint}**. Wees extra alert op hun format."

    system_prompt = f"""Je bent een precieze factuur-extractor voor een Belgische eenmanszaak (Compact Living, camperbouw).
Lees de factuur en extraheer gestructureerde data in EXACT dit JSON-schema:

{EXTRACT_SCHEMA}

Belangrijke regels:
- Geef ALLEEN geldig JSON terug, geen markdown, geen uitleg
- Voor BTW: gebruik percentage (21, 6, 0). Bij intracommunautaire levering met 0% BTW (verlegging): zet 0
- Voor hoeveelheden: probeer eenheid te detecteren (stuks/m²/kg) maar gebruik gewoon het cijfer
- Voor onbekende velden: gebruik null, niet "?", niet ""
- Voor lijnen: groepeer NIET, behoud elke factuurlijn apart
- Zorg dat sum(lijnen.totaal_excl_btw) ≈ factuur.totaal_excl_btw (max 1% afwijking)
{hint_text}"""

    pdf_b64 = pdf_to_base64(pdf_path)

    response = client.messages.create(
        model=model,
        max_tokens=8000,
        system=system_prompt,
        messages=[{
            "role": "user",
            "content": [
                {
                    "type": "document",
                    "source": {"type": "base64", "media_type": "application/pdf", "data": pdf_b64}
                },
                {"type": "text", "text": "Extraheer deze factuur volgens het schema. Geef alleen JSON terug."}
            ]
        }]
    )

    text = response.content[0].text.strip()
    # Verwijder markdown code fences indien aanwezig
    if text.startswith("```"):
        text = text.split("```")[1]
        if text.startswith("json"):
            text = text[4:]
        text = text.strip()
    return json.loads(text)
