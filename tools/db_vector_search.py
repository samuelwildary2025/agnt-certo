"""
Busca vetorial de produtos usando pgvector no PostgreSQL.
Substitui a busca por trigram (db_search.py) por busca semântica com embeddings.
"""
import json
import psycopg2
from pathlib import Path
from psycopg2.extras import RealDictCursor
from openai import OpenAI
from config.settings import settings
from config.logger import setup_logger
from typing import Dict, Optional

logger = setup_logger(__name__)

# Cliente OpenAI para gerar embeddings
_openai_client = None
_TERM_TRANSLATIONS_CACHE: Optional[Dict[str, object]] = None

def _load_extra_term_translations() -> Dict[str, str]:
    global _TERM_TRANSLATIONS_CACHE
    path = str(getattr(settings, "term_translations_path", "prompts/term_translations.json") or "").strip()
    if not path:
        return {}

    file_path = Path(path)
    if not file_path.is_absolute():
        base_dir = Path(__file__).resolve().parent.parent
        file_path = base_dir / file_path

    try:
        stat = file_path.stat()
    except Exception:
        return {}

    mtime = int(stat.st_mtime)
    if _TERM_TRANSLATIONS_CACHE and _TERM_TRANSLATIONS_CACHE.get("mtime") == mtime:
        return _TERM_TRANSLATIONS_CACHE.get("data") or {}

    try:
        raw = file_path.read_text(encoding="utf-8").strip()
        parsed = json.loads(raw) if raw else {}
        if not isinstance(parsed, dict):
            parsed = {}
        cleaned: Dict[str, str] = {}
        for k, v in parsed.items():
            if isinstance(k, str) and isinstance(v, str):
                kk = k.strip()
                vv = v.strip()
                if kk and vv:
                    cleaned[kk] = vv
        _TERM_TRANSLATIONS_CACHE = {"mtime": mtime, "data": cleaned}
        return cleaned
    except Exception as e:
        logger.error(f"Erro lendo term_translations.json: {e}")
        _TERM_TRANSLATIONS_CACHE = {"mtime": mtime, "data": {}}
        return {}

# FlashRank removido - O Analista (sub-agente LLM) decide o melhor produto

def _get_openai_client() -> OpenAI:
    """Retorna cliente OpenAI singleton."""
    global _openai_client
    if _openai_client is None:
        # Prioriza chave específica de embedding, senão usa a geral
        api_key = getattr(settings, "openai_embedding_api_key", None) or settings.openai_api_key
        
        if not api_key:
            raise ValueError("OPENAI_EMBEDDING_API_KEY ou OPENAI_API_KEY não configurada")

        # FIX: httpx 0.28.1 removed 'proxies' arg, causing error in OpenAI client init
        # We explicitly pass a pre-configured httpx client to avoid this.
        import httpx
        http_client = httpx.Client()
        
        _openai_client = OpenAI(
            api_key=api_key, 
            base_url="https://api.openai.com/v1",
            http_client=http_client
        )
    return _openai_client


def _generate_embedding(text: str) -> list[float]:
    """
    Gera embedding para um texto usando OpenAI.
    Usa o modelo text-embedding-3-small (1536 dimensões).
    """
    client = _get_openai_client()
    
    # Limpar e normalizar o texto
    text = text.strip()
    if not text:
        raise ValueError("Texto vazio para embedding")
    
    response = client.embeddings.create(
        model="text-embedding-3-small",
        input=text
    )
    
    return response.data[0].embedding


def search_products_vector(query: str, limit: int = 20) -> str:
    """
    Busca produtos por similaridade vetorial usando pgvector.
    
    Args:
        query: Texto de busca (nome do produto, descrição, etc.)
        limit: Número máximo de resultados (default: 20)
    
    Returns:
        String formatada com EANs encontrados no formato:
        EANS_ENCONTRADOS:
        1) 123456789 - PRODUTO A
        2) 987654321 - PRODUTO B
    """
    # Connection string do banco vetorial
    conn_str = settings.vector_db_connection_string
    if not conn_str:
        conn_str = getattr(settings, "products_db_connection_string", None)
    
    if not conn_str:
        return "Erro: String de conexão do banco vetorial não configurada."
    
    query = query.strip()
    if not query:
        return "Nenhum termo de busca informado."
    
    # Lista de produtos que são tipicamente hortifruti (frutas, legumes, verduras)
    # Quando detectamos um desses, adicionamos contexto para melhorar a busca
    HORTIFRUTI_KEYWORDS = [
        "tomate", "cebola", "batata", "alface", "cenoura", "pepino", "pimentao",
        "abobora", "abobrinha", "berinjela", "beterraba", "brocolis", "couve",
        "espinafre", "repolho", "rucula", "agriao", "alho", "gengibre", "mandioca",
        "banana", "maca", "laranja", "limao", "abacaxi", "melancia", "melao",
        "uva", "morango", "manga", "mamao", "abacate", "goiaba", "pera", "pessego",
        "ameixa", "kiwi", "coco", "maracuja", "acerola", "caju", "pitanga",
        "cheiro verde", "coentro", "salsa", "cebolinha", "hortela", "manjericao",
        "alecrim", "tomilho", "oregano", "louro", 
       
    ]
    
    TERM_TRANSLATIONS = _load_extra_term_translations()
    
    
    # ---------------------------------------------------------
    # 🆕 REGRA GLOBAL: REMOVER ACENTOS (Pedido do usuário)
    # O banco de dados não tem acentos (ex: "vô" -> "vo", "maçã" -> "maca").
    # Removemos logo no início para garantir match FTS/Vetorial.
    # ---------------------------------------------------------
    import unicodedata
    def remove_accents(input_str):
        nfkd_form = unicodedata.normalize('NFKD', input_str)
        return "".join([c for c in nfkd_form if not unicodedata.combining(c)])

    raw_query = query
    query = remove_accents(query)
    query_lower = query.lower().strip()

    mode = str(getattr(settings, "vector_search_mode", "exact") or "exact").lower().strip()
    use_fallback = bool(getattr(settings, "vector_search_fallback", True))

    def _apply_translations(q: str) -> str:
        q_lower_local = q.lower().strip()
        out = q
        sorted_translations = sorted(TERM_TRANSLATIONS.items(), key=lambda x: len(x[0]), reverse=True)
        for term, abbreviation in sorted_translations:
            term_clean = remove_accents(term)
            if term_clean in q_lower_local:
                out = out.lower().replace(term_clean, abbreviation)
                logger.info(f"🔄 [TRADUÇÃO PARCIAL] '{term_clean}' → '{abbreviation}' | Result: '{out}'")
                break
        return out

    def _apply_boost(q: str) -> str:
        q_lower_local = q.lower()
        PROCESSED_TERMS = [
            "doce", "suco", "molho", "extrato", "polpa", "geleia", "compota", "refresco", "refrescou",
            "rufles", "ruffles", "batata palha", "batata chips", "chips", "salgadinho", "snack",
            "cheetos", "fandangos", "doritos", "pringles", "stax", "baconzitos", "cebolitos"
        ]
        is_processed = any(term in q_lower_local for term in PROCESSED_TERMS)

        if is_processed:
            logger.info("⏭️ [BOOST SKIP] Produto processado detectado, pulando boost hortifruti")
            return q

        import re
        query_to_check = q.lower()
        for keyword in HORTIFRUTI_KEYWORDS:
            if re.search(r"\b" + re.escape(keyword) + r"\b", query_to_check):
                if keyword in ["frango"]:
                    boosted = f"{q} açougue carnes abatido resfriado"
                elif keyword in ["carne", "peixe"]:
                    boosted = f"{q} açougue carnes"
                elif keyword in ["ovo", "leite", "queijo", "manteiga", "iogurte"]:
                    boosted = f"{q} laticínios"
                else:
                    boosted = f"{q} hortifruti legumes verduras frutas"
                logger.info(f"🎯 [BOOST] Query melhorada: '{boosted}'")
                return boosted
        return q

    enhanced_query = query
    if mode != "exact":
        enhanced_query = _apply_translations(enhanced_query)
        if mode == "assist":
            enhanced_query = _apply_boost(enhanced_query)

    logger.info(f"🔍 [VECTOR SEARCH] mode={mode} | query='{query}'" + (f" → '{enhanced_query}'" if enhanced_query != query else ""))
    
    try:
        def _hybrid_search(text_query: str):
            query_embedding = _generate_embedding(text_query)
            with psycopg2.connect(conn_str) as conn:
                with conn.cursor(cursor_factory=RealDictCursor) as cur:
                    embedding_str = f"[{','.join(map(str, query_embedding))}]"
                    sql = """
                        SELECT 
                            h.text,
                            h.metadata,
                            h.score as similarity,
                            h.rank
                        FROM hybrid_search_v2(
                            %s,                    -- query_text
                            %s::vector,            -- query_embedding
                            %s,                    -- match_count
                            1.0,                   -- full_text_weight
                            1.0,                   -- semantic_weight
                            0.5,                   -- setor_boost (HORTI-FRUTI/FRIGORIFICO)
                            50                     -- rrf_k (parâmetro RRF)
                        ) h
                    """
                    cur.execute(sql, (text_query, embedding_str, limit))
                    return cur.fetchall()

        results = _hybrid_search(enhanced_query)

        if (not results) and mode == "exact" and use_fallback:
            fallback_query = _apply_translations(query)
            fallback_query = _apply_boost(fallback_query)
            if fallback_query != enhanced_query:
                logger.info(f"🛟 [VECTOR SEARCH] fallback: '{enhanced_query}' → '{fallback_query}'")
                results = _hybrid_search(fallback_query)
                
                logger.info(f"🔍 [VECTOR SEARCH] Encontrados {len(results)} resultados")
                
                # LOG detalhado para debug de relevância
                if results:
                    import re
                    # Filtrar resultados válidos (e previnir None)
                    valid_results = []
                    for r in results:
                        if not r or r.get("text") is None:
                            continue
                        # Garantir que text seja string
                        if not isinstance(r["text"], str):
                            r["text"] = str(r["text"] or "")
                        valid_results.append(r)
                    
                    results = valid_results

                    for i, r in enumerate(results[:5]):  # Top 5 para debug
                        text = r.get("text", "")
                        sim = r.get("similarity", 0)
                        match = re.search(r'"produto":\s*"([^"]+)"', text)
                        nome = match.group(1) if match else text[:40]
                        cat_match = re.search(r'"categoria1":\s*"([^"]+)"', text)
                        cat = cat_match.group(1) if cat_match else ""
                        logger.debug(f"   {i+1}. [{sim:.4f}] {nome} | {cat}")

        if not results:
            return "Nenhum produto encontrado com esse termo."

        return _format_results(results)
    
    except Exception as e:
        logger.error(f"❌ Erro na busca vetorial: {e}")
        return f"Erro ao buscar no banco vetorial: {str(e)}"


def _extract_ean_and_name(result: dict) -> tuple[str, str]:
    """
    Extrai EAN e nome do produto do resultado.
    O n8n salva os dados em 'text' (conteúdo) e 'metadata' (JSON).
    """
    text_raw = (result or {}).get("text", "")
    if text_raw is None:
        text = ""
    elif isinstance(text_raw, str):
        text = text_raw
    else:
        text = str(text_raw)

    metadata = (result or {}).get("metadata") or {}
    
    # Tentar extrair do metadata primeiro (mais confiável)
    if isinstance(metadata, str):
        try:
            metadata = json.loads(metadata)
        except:
            metadata = {}
    elif not isinstance(metadata, dict):
        metadata = {}
    
    ean = ""
    nome = ""
    
    # Buscar EAN no metadata ou no texto
    if metadata:
        ean = str(metadata.get("codigo_ean", metadata.get("ean", "")) or "")
        nome = str(metadata.get("produto", metadata.get("nome", "")) or "")
    
    # Se não achou no metadata, parsear do texto
    if not ean or not nome:
        # O texto pode estar no formato: {"codigo_ean": 123, "produto": "NOME"}
        import re
        
        # Tentar encontrar codigo_ean no texto
        ean_match = re.search(r'"codigo_ean":\s*"?(\d+)"?', text)
        if ean_match:
            ean = ean_match.group(1)
        
        # Tentar encontrar produto no texto
        nome_match = re.search(r'"produto":\s*"([^"]+)"', text)
        if nome_match:
            nome = nome_match.group(1)
            
    # Fallback 2: O texto está no formato cru "ean 12345 NOME DO PRODUTO..."
    if not ean:
        import re
        # Procura por "ean 12345" (case insensitive)
        raw_match = re.search(r'ean\s+(\d+)\s+(.*)', text, re.IGNORECASE)
        if raw_match:
            ean = raw_match.group(1)
            # Se não tiver nome ainda, usa o resto da string
            if not nome:
                nome = raw_match.group(2).strip()
    
    # Fallback: usar o texto inteiro como nome
    if not nome and text:
        nome = text[:100]  # Truncar se muito longo
    
    return ean, nome


def _format_results(results: list[dict]) -> str:
    """Formata lista de resultados para o formato esperado pelo agente."""
    lines = ["EANS_ENCONTRADOS:"]
    seen_eans = set()  # Evitar duplicatas
    
    for i, row in enumerate(results, 1):
        ean, nome = _extract_ean_and_name(row)
        similarity = row.get("similarity", 0)
        
        # Pular se não tem EAN ou se já vimos esse EAN
        if not ean or ean in seen_eans:
            continue
        
        seen_eans.add(ean)
        
        # Formatar com score de similaridade para debug
        logger.debug(f"   {i}. {nome} (EAN: {ean}) [Similarity: {similarity:.3f}]")
        
        if ean and nome:
            lines.append(f"{len(seen_eans)}) {ean} - {nome}")
    
    if len(lines) == 1:  # Só tem o header
        return "Nenhum produto com EAN válido encontrado."
    
    return "\n".join(lines)
