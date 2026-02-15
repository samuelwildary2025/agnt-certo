
import json
import re
import unicodedata
from typing import Any, Dict, List, Optional

import psycopg2
from psycopg2 import sql
from psycopg2.extras import RealDictCursor

from config.settings import settings
from config.logger import setup_logger
from tools.redis_tools import save_suggestions

logger = setup_logger(__name__)

_TERM_TRANSLATIONS_CACHE: Optional[Dict[str, str]] = None

_UNIT_NORMALIZATION = {
    "lts": "l",
    "lt": "l",
    "litro": "l",
    "litros": "l",
    "l": "l",
    "ml": "ml",
    "g": "g",
    "kg": "kg",
}


def _normalize_units(text: str) -> str:
    t = (text or "").strip().lower()
    if not t:
        return t

    t = t.replace(" ", "")

    def repl(m: re.Match) -> str:
        num = m.group(1)
        unit = m.group(2).lower()
        unit = _UNIT_NORMALIZATION.get(unit, unit)
        return f"{num}{unit}"

    t = re.sub(r"(\d+(?:[\.,]\d+)?)(lts|lt|litros|litro|l|kg|g|ml)\b", repl, t)
    return t


def _normalize_units_in_text(text: str) -> str:
    s = (text or "").strip().lower()
    if not s:
        return s

    def repl(m: re.Match) -> str:
        num = m.group(1)
        unit = m.group(2).lower()
        unit = _UNIT_NORMALIZATION.get(unit, unit)
        return f"{num}{unit}"

    return re.sub(r"(\d+(?:[\.,]\d+)?)\s*(lts|lt|litros|litro|l|kg|g|ml)\b", repl, s)


def _extract_unit_token(query: str) -> Optional[str]:
    q = (query or "").lower()
    m = re.search(r"\b(\d+(?:[\.,]\d+)?)(l|kg|g|ml)\b", q)
    if not m:
        return None
    num = m.group(1).replace(",", ".")
    unit = m.group(2)
    return f"{num}{unit}"


def _text_has_unit(text: str, unit_token: str) -> bool:
    if not text or not unit_token:
        return False
    m = re.match(r"^(\d+(?:\.\d+)?)(l|kg|g|ml)$", unit_token)
    if not m:
        return False
    num = re.escape(m.group(1))
    unit = re.escape(m.group(2))
    pattern = re.compile(rf"\b{num}\s*{unit}\b", re.IGNORECASE)
    return bool(pattern.search(text))


def _normalize_query_text(text: str) -> str:
    text = (text or "").strip()
    text = re.sub(r"\s+", " ", text)
    return text


def _load_term_translations() -> Dict[str, str]:
    global _TERM_TRANSLATIONS_CACHE
    if _TERM_TRANSLATIONS_CACHE is not None:
        return _TERM_TRANSLATIONS_CACHE
    path = getattr(settings, "term_translations_path", "") or ""
    if not path:
        _TERM_TRANSLATIONS_CACHE = {}
        return _TERM_TRANSLATIONS_CACHE
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            _TERM_TRANSLATIONS_CACHE = {
                str(k).strip().lower(): str(v).strip() for k, v in data.items() if k and v
            }
        else:
            _TERM_TRANSLATIONS_CACHE = {}
    except Exception:
        _TERM_TRANSLATIONS_CACHE = {}
    return _TERM_TRANSLATIONS_CACHE


def _apply_term_translations(query: str) -> str:
    q = (query or "").strip()
    if not q:
        return q

    q_low = q.lower()
    tokens = q_low.split(" ")

    drop_tokens = {
        "de",
        "da",
        "do",
        "das",
        "dos",
        "a",
        "o",
        "as",
        "os",
        "um",
        "uma",
        "uns",
        "umas",
    }
    cleaned_tokens = [t for t in tokens if t and t not in drop_tokens]

    content_tokens = [
        t for t in cleaned_tokens if t and not re.fullmatch(r"\d+(?:[\.,]\d+)?x?", t)
    ]
    if len(content_tokens) == 1:
        t = content_tokens[0]
        if t in {"calabresa", "calabresas", "calabrasa", "calabrasas", "calabrezas"}:
            return "linguica calabresa"

    translations = _load_term_translations()
    if not translations:
        return " ".join(cleaned_tokens).strip() or q

    replaced = [translations.get(w, w) for w in cleaned_tokens]
    out = " ".join(replaced).strip()
    return out or q


def _strip_accents(text: str) -> str:
    if not text:
        return ""
    return "".join(
        ch
        for ch in unicodedata.normalize("NFKD", text)
        if not unicodedata.combining(ch)
    )


def _safe_float(v: Any, default: float = 0.0) -> float:
    try:
        if v is None:
            return default
        return float(v)
    except Exception:
        return default


def _format_results(rows: List[Dict[str, Any]]) -> str:
    output: List[Dict[str, Any]] = []
    for row in rows:
        output.append(
            {
                "id": row.get("id"),
                "nome": row.get("nome") or "Produto sem nome",
                "preco": _safe_float(row.get("preco"), 0.0),
                "estoque": _safe_float(row.get("estoque"), 0.0),
                "unidade": row.get("unidade") or "UN",
                "categoria": row.get("categoria") or "",
            }
        )
    return json.dumps(output, ensure_ascii=False)


def search_products_db(query: str, limit: int = 8, telefone: Optional[str] = None) -> str:
    """Busca produtos no Postgres.

    Estratégia (tentativas em cascata):
    1) Busca híbrida (FTS + trigram + ILIKE) se extensões existirem
    2) Fallback para ILIKE com unaccent
    3) Fallback final para ILIKE simples (sem unaccent)

    Retorna SEMPRE um JSON (lista) para manter o contrato da tool.
    """

    q = _normalize_query_text(query)
    q = _apply_term_translations(q)

    q = _normalize_units_in_text(q)
    q = re.sub(r"\s+", " ", q).strip()
    desired_unit = _extract_unit_token(q)
    if len(q) < 2:
        return "[]"

    raw_for_fts = q
    q_no_accents = _strip_accents(q)

    configured_table_name = settings.postgres_products_table_name or "produtos-sp-queiroz"
    limit = max(1, min(int(limit or 8), 25))

    conn = None
    cursor = None
    try:
        conn = psycopg2.connect(settings.postgres_connection_string)
        cursor = conn.cursor(cursor_factory=RealDictCursor)

        cursor.execute(
            "select extname from pg_extension where extname in ('unaccent','pg_trgm')"
        )
        available_exts = {r["extname"] for r in (cursor.fetchall() or [])}
        has_unaccent = "unaccent" in available_exts
        has_trgm = "pg_trgm" in available_exts

        like_term = f"%{q}%"
        like_term_no_accents = f"%{q_no_accents}%"

        def candidate_table_names(name: str) -> List[str]:
            base = (name or "").strip() or "produtos-sp-queiroz"
            variants = [base]
            if "produtos-" in base:
                variants.append(base.replace("produtos-", "produto-", 1))
            if "produto-" in base:
                variants.append(base.replace("produto-", "produtos-", 1))
            out: List[str] = []
            seen = set()
            for t in variants:
                if t and t not in seen:
                    out.append(t)
                    seen.add(t)
            return out

        results: List[Dict[str, Any]] = []
        last_error: Optional[Exception] = None

        for table_name in candidate_table_names(configured_table_name):
            table_ident = sql.Identifier(table_name)
            queries = []

            # 1) Híbrida: FTS + trigram + ILIKE (melhor relevância quando disponível)
            if has_unaccent and has_trgm:
                queries.append(
                    (
                        sql.SQL(
                            """
                            WITH q AS (
                                SELECT plainto_tsquery('simple', unaccent(%s)) AS tsq
                            )
                            SELECT id, nome, preco, estoque, unidade, categoria
                            FROM {table}
                            CROSS JOIN q
                            WHERE (
                                to_tsvector('simple', unaccent(coalesce(nome,'') || ' ' || coalesce(descricao,''))) @@ q.tsq
                                OR unaccent(nome) ILIKE unaccent(%s)
                                OR unaccent(descricao) ILIKE unaccent(%s)
                                OR word_similarity(unaccent(%s), unaccent(nome)) > 0.2
                                OR word_similarity(unaccent(%s), unaccent(descricao)) > 0.2
                                OR similarity(unaccent(nome), unaccent(%s)) > 0.2
                                OR similarity(unaccent(descricao), unaccent(%s)) > 0.2
                            )
                            ORDER BY (
                                0.70 * ts_rank_cd(
                                    to_tsvector('simple', unaccent(coalesce(nome,'') || ' ' || coalesce(descricao,''))),
                                    q.tsq
                                )
                                + 0.30 * GREATEST(
                                    word_similarity(unaccent(%s), unaccent(nome)),
                                    word_similarity(unaccent(%s), unaccent(descricao)),
                                    similarity(unaccent(nome), unaccent(%s)),
                                    similarity(unaccent(descricao), unaccent(%s))
                                )
                            ) DESC
                            LIMIT %s
                            """
                        ).format(table=table_ident),
                        (
                            raw_for_fts,
                            like_term,
                            like_term,
                            q,
                            q,
                            q,
                            q,
                            q,
                            q,
                            q,
                            q,
                            limit,
                        ),
                    )
                )

                queries.append(
                    (
                        sql.SQL(
                            """
                            SELECT id, nome, preco, estoque, unidade, categoria
                            FROM {table}
                            WHERE (
                                word_similarity(unaccent(%s), unaccent(nome)) > 0.2
                                OR word_similarity(unaccent(%s), unaccent(descricao)) > 0.2
                            )
                            ORDER BY GREATEST(
                                word_similarity(unaccent(%s), unaccent(nome)),
                                word_similarity(unaccent(%s), unaccent(descricao))
                            ) DESC
                            LIMIT %s
                            """
                        ).format(table=table_ident),
                        (q, q, q, q, limit),
                    )
                )

            # 2) ILIKE com unaccent (mais simples, ainda bem útil)
            if has_unaccent:
                queries.append(
                    (
                        sql.SQL(
                            """
                            SELECT id, nome, preco, estoque, unidade, categoria
                            FROM {table}
                            WHERE unaccent(nome) ILIKE unaccent(%s)
                               OR unaccent(descricao) ILIKE unaccent(%s)
                            LIMIT %s
                            """
                        ).format(table=table_ident),
                        (like_term, like_term, limit),
                    )
                )

            # 3) ILIKE sem unaccent (fallback final se a extensão unaccent não existir)
            queries.append(
                (
                    sql.SQL(
                        """
                        SELECT id, nome, preco, estoque, unidade, categoria
                        FROM {table}
                        WHERE nome ILIKE %s
                           OR descricao ILIKE %s
                           OR nome ILIKE %s
                           OR descricao ILIKE %s
                        LIMIT %s
                        """
                    ).format(table=table_ident),
                    (like_term, like_term, like_term_no_accents, like_term_no_accents, limit),
                )
            )

            # 4) Só por nome (se a tabela não tiver coluna descricao)
            queries.append(
                (
                    sql.SQL(
                        """
                        SELECT id, nome, preco, estoque, unidade, categoria
                        FROM {table}
                        WHERE nome ILIKE %s
                           OR nome ILIKE %s
                        LIMIT %s
                        """
                    ).format(table=table_ident),
                    (like_term, like_term_no_accents, limit),
                )
            )

            for query_sql, params in queries:
                try:
                    cursor.execute(query_sql, params)
                    results = cursor.fetchall() or []
                    last_error = None
                    break
                except Exception as e:
                    last_error = e
                    continue

            if last_error is None:
                break

        if last_error is not None:
            logger.error(f"Erro na busca DB (todas tentativas falharam): {last_error}")
            return "[]"

        if desired_unit and results:
            filtered = [
                r
                for r in results
                if _text_has_unit(r.get("nome") or "", desired_unit)
                or _text_has_unit(r.get("descricao") or "", desired_unit)
            ]
            if filtered:
                results = filtered

        json_str = _format_results(results)

        if telefone:
            try:
                products_for_cache = []
                for r in results:
                    products_for_cache.append(
                        {
                            "nome": r.get("nome") or "",
                            "preco": _safe_float(r.get("preco"), 0.0),
                            "termo_busca": q,
                        }
                    )
                save_suggestions(telefone, products_for_cache)
            except Exception as e:
                logger.warning(f"Falha ao salvar sugestões no Redis: {e}")

        return json_str
    except Exception as e:
        logger.error(f"Erro na busca DB: {e}")
        return "[]"
    finally:
        try:
            if cursor is not None:
                cursor.close()
        except Exception:
            pass
        try:
            if conn is not None:
                conn.close()
        except Exception:
            pass
