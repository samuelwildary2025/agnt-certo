
import json
import re
import unicodedata
import difflib
from typing import Any, Dict, List, Optional
import threading

import psycopg2
import psycopg2.pool
from psycopg2 import sql
from psycopg2.extras import RealDictCursor

from config.settings import settings
from config.logger import setup_logger
from tools.redis_tools import save_suggestions

logger = setup_logger(__name__)

# Connection pool (singleton, thread-safe)
_db_pool: Optional[psycopg2.pool.SimpleConnectionPool] = None
_pool_lock = threading.Lock()

def _get_connection():
    """Obtém uma conexão do pool (ou cria o pool na primeira chamada)."""
    global _db_pool
    if _db_pool is None or _db_pool.closed:
        with _pool_lock:
            if _db_pool is None or _db_pool.closed:
                try:
                    _db_pool = psycopg2.pool.SimpleConnectionPool(
                        minconn=1,
                        maxconn=5,
                        dsn=settings.postgres_connection_string
                    )
                    logger.info("🔌 Pool de conexões Postgres criado (min=1, max=5)")
                except Exception as e:
                    logger.error(f"Falha ao criar pool Postgres: {e}")
                    # Fallback para conexão direta
                    return psycopg2.connect(settings.postgres_connection_string)
    try:
        return _db_pool.getconn()
    except Exception as e:
        logger.warning(f"Pool esgotado, criando conexão direta: {e}")
        return psycopg2.connect(settings.postgres_connection_string)

def _return_connection(conn):
    """Devolve a conexão ao pool."""
    global _db_pool
    if _db_pool is not None and not _db_pool.closed:
        try:
            _db_pool.putconn(conn)
            return
        except Exception:
            pass
    # Se pool não disponível, fecha diretamente
    try:
        conn.close()
    except Exception:
        pass


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

    # FASE 1: Substituições multi-palavra (ex: "frango inteiro" → "frango abatido")
    # Checar pares e trios de tokens contra o dicionário
    multi_keys = {k for k in translations if " " in k}
    joined = " ".join(cleaned_tokens)
    for mk in sorted(multi_keys, key=len, reverse=True):  # mais longo primeiro
        if mk in joined:
            joined = joined.replace(mk, translations[mk])
    
    # FASE 2: Substituições de palavra individual para tokens restantes
    final_tokens = joined.split(" ")
    replaced = [translations.get(w, w) for w in final_tokens if w]
    out = " ".join(replaced).strip()
    
    # FASE 3: Regra geral para Hortifruti -> adicionar "kg"
    # Se o cliente busca por uma fruta ou legume simples (ex: "maca", "banana", "cenoura")
    # quer levar a versão in natura (vendida por kg) e não produtos industrializados
    HORTI_CONHECIDOS = {
        # Frutas
        "abacate", "abacaxi", "acerola", "ameixa", "amora", "banana", "caju",
        "carambola", "cereja", "coco", "cupuacu", "figo", "framboesa", "goiaba",
        "graviola", "jabuticaba", "jaca", "jamelao", "kiwi", "laranja", "limao",
        "maca", "mamao", "manga", "maracuja", "melancia", "melao", "morango",
        "nectarina", "pera", "pessego", "pitanga", "pitaya", "roma", "tangerina", "uva",
        # Legumes e Verduras
        "abobora", "abobrinha", "acelga", "agriao", "aipo", "alface", "alho",
        "alho-poro", "almeirao", "aspargo", "batata", "batata-doce", "berinjela",
        "beterraba", "brocolis", "cebola", "cebolinha", "cenoura", "chicoria",
        "chuchu", "coentro", "couve", "couve-flor", "espinafre", "inhame", "jilo",
        "mandioca", "mandioquinha", "maxixe", "milho", "nabo", "palmito", "pepino",
        "pimentao", "quibebe", "quiabo", "rabanete", "repolho", "rucula", "salsa", "tomate", "vagem"
    }
    
    # Ignorar a regra de adicionar "kg" se a busca contiver palavras que remetem a processados
    PROCESSADOS_KEYWORDS = {"suco", "doce", "polpa", "bala", "biscoito", "bolacha", "bolo", "sorvete", "picolé", "picole", "gelatina", "iogurte", "geleia", "barrinha", "creme", "oleo", "chips"}
    
    # Primeiro normalizamos a saída sem acentos
    out_no_accents = _strip_accents(out.lower())
    
    # Converter a string 'out' inteira removendo "s" de palavras longas antes da inserção do kg 
    # para que plural não quebre a pesquisa por prefixos
    words = out.split()
    corrected_words = []
    
    for w in words:
        w_clean = _strip_accents(w.lower())
        if len(w_clean) > 3 and w_clean.endswith("s"):
            # Remover 's' final do plural na query (mantendo os acentos originais se existirem)
            corrected_words.append(w[:-1])
            out_no_accents = out_no_accents.replace(w_clean, w_clean[:-1])
        else:
            corrected_words.append(w)
            
    out = " ".join(corrected_words).strip()
    words_singular = out_no_accents.split()
            
    # Se já tem "kg" na string normalizada, obviamente não precisa colocar de novo
    if "kg" not in words_singular:
        # Verificar se não há nenhuma palavra de processado
        if not any(k in words_singular for k in PROCESSADOS_KEYWORDS):
            # Se encontrar ao menos UM hortifruti conhecido na string
            if any(f in words_singular for f in HORTI_CONHECIDOS):
                out = out + " kg"

    return out or q


def _strip_accents(text: str) -> str:
    if not text:
        return ""
    return "".join(
        ch
        for ch in unicodedata.normalize("NFKD", text)
        if not unicodedata.combining(ch)
    )


def _tokenize_for_match(text: str) -> List[str]:
    t = _strip_accents((text or "").lower())
    t = re.sub(r"[^a-z0-9]+", " ", t)
    tokens = [x for x in t.split(" ") if x]
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
        "e",
    }
    return [t for t in tokens if t and t not in drop_tokens]


def _score_match(query: str, name: str, category: str) -> float:
    q_tokens = _tokenize_for_match(_normalize_units_in_text(query))
    if not q_tokens:
        return 0.0
    name_tokens = _tokenize_for_match(name)
    category_tokens = _tokenize_for_match(category)
    candidate_tokens = set(name_tokens + category_tokens)
    overlap = len(set(q_tokens) & candidate_tokens) / max(len(set(q_tokens)), 1)
    q_norm = " ".join(q_tokens)
    name_norm = " ".join(name_tokens)
    if not name_norm:
        return round(overlap, 4)
    ratio = difflib.SequenceMatcher(None, q_norm, name_norm).ratio()
    return round(0.6 * overlap + 0.4 * ratio, 4)


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
        estoque_val = _safe_float(row.get("estoque"), 0.0)
        categoria = row.get("categoria") or ""
        # Frigorífico e Hortifruti sempre disponíveis (vendido por peso ou variável)
        # Palavras-chave que indicam produtos que não devem validar estoque zerado
        keywords_ignore = ["frigori", "acougue", "açougue", "bovinos", "horti", "legume", "verdura", "fruta", "aves", "frios", "embutidos", "flv"]
        nome_lower = (row.get("nome") or "").lower()
        is_ignora_estoque = any(k in categoria.lower() for k in keywords_ignore)
        
        # Fallback: se o nome termina com "kg" e categoria não é limpeza/higiene/bebida/mercearia, provavelmente é produto fresco
        if not is_ignora_estoque and nome_lower.strip().endswith("kg"):
            categorias_excluidas = ["limpeza", "higiene", "bebida", "mercearia"]
            if not any(c in categoria.lower() for c in categorias_excluidas):
                is_ignora_estoque = True
        
        # Se for um desses itens e estoque vier zerado/negativo, forçamos um valor positivo
        if is_ignora_estoque and estoque_val <= 0:
             estoque_val = 100.0
             
        sem_estoque = estoque_val <= 0 and not is_ignora_estoque
        
        # Produtos sem estoque são completamente omitidos dos resultados
        if sem_estoque:
            continue
        
        item = {
            "id": row.get("id"),
            "nome": row.get("nome") or "Produto sem nome",
            "categoria": categoria,
            "preco": _safe_float(row.get("preco"), 0.0),
            "estoque": estoque_val,
            "unidade": row.get("unidade") or "UN",
            "match_score": _safe_float(row.get("match_score"), 0.0),
            "match_ok": bool(row.get("match_ok")),
        }
        output.append(item)
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
    is_pool_conn = True
    cursor = None
    try:
        conn = _get_connection()
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

        if results:
            for r in results:
                score = _score_match(q, r.get("nome") or "", r.get("categoria") or "")
                r["match_score"] = score
                r["match_ok"] = score >= 0.55
            results = sorted(results, key=lambda r: r.get("match_score", 0.0), reverse=True)

            # PRIORIZAÇÃO 1: Frango → abatido sempre primeiro
            PRIORITY_BOOST = {
                "frango": "abatido",
                "calabresa": "kg",
                "moida": "primeira",
                "moido": "primeira",
                "kisuki": "refresco",
                "refresco": "po",
                "creme leite": "creme",
                "alho": "kg",
                "abacaxi": "kg",
                "laranja": "kg",
            }
            q_lower = q.lower()
            for termo, boost_word in PRIORITY_BOOST.items():
                if termo in q_lower:
                    boosted = [r for r in results if boost_word in (r.get("nome") or "").lower()]
                    others = [r for r in results if boost_word not in (r.get("nome") or "").lower()]
                    if boosted:
                        results = boosted + others
                        logger.info(f"⬆️ Priorização: '{boost_word}' movido para o topo da busca '{q}'")
                    break

            # PRIORIZAÇÃO 2: Frutas/Legumes/Verduras — produtos com "KG" no nome vêm primeiro
            # Ex: "TOMATE KG", "MELANCIA KG", "CEBOLA KG" devem aparecer antes de versões industrializadas
            HORTI_CATEGORIES = ["horti", "fruta", "legume", "verdura", "flv"]
            has_horti_results = any(
                any(k in (r.get("categoria") or "").lower() for k in HORTI_CATEGORIES)
                for r in results
            )
            if has_horti_results:
                kg_boosted = [r for r in results if (r.get("nome") or "").upper().strip().endswith("KG")]
                kg_others = [r for r in results if not (r.get("nome") or "").upper().strip().endswith("KG")]
                if kg_boosted:
                    results = kg_boosted + kg_others
                    logger.info(f"⬆️ Priorização Horti: {len(kg_boosted)} produto(s) KG movido(s) para o topo")

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
                            "match_ok": bool(r.get("match_ok")),
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
                _return_connection(conn)
        except Exception:
            pass
