import sys
sys.path.append('.')
from tools.db_search import _normalize_units_in_text, _apply_term_translations, _extract_unit_token

queries = [
    "banana",
    "maca",
    "uva",
    "morango",
    "laranja",
    "melancia",
    "abacate",
    "banana maçã",
    "1kg de carne",
    "doce de leite",
    "suco de laranja"
]

for q in queries:
    print(f"Query: {q}")
    translated = _apply_term_translations(q)
    print(f"  Translated: {translated}")
    normalized_units = _normalize_units_in_text(translated)
    print(f"  Norm Units: {normalized_units}")
    unit = _extract_unit_token(normalized_units)
    print(f"  Unit: {unit}")
    print("-" * 20)
