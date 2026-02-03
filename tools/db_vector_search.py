"""
Busca vetorial de produtos usando pgvector no PostgreSQL.
Substitui a busca por trigram (db_search.py) por busca semântica com embeddings.
"""
import json
import psycopg2
from psycopg2.extras import RealDictCursor
from openai import OpenAI
from config.settings import settings
from config.logger import setup_logger

logger = setup_logger(__name__)

# Cliente OpenAI para gerar embeddings
_openai_client = None

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
        # Fallback para o banco de produtos padrão
        conn_str = settings.products_db_connection_string
    
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
    
    # Traduções de termos comuns para abreviações usadas no banco
    TERM_TRANSLATIONS = {
        "absorvente": "abs",
        "achocolatado": "achoc",
        "refrigerante": "refrig",
        "amaciante": "amac",
        "desodorante": "desod",
        "shampoo": "sh",
        "condicionador": "cond",
        "hotdog": "pao hot dog maxpaes",
        "cachorro quente": "pao hot dog maxpaes",
        "cachorro-quente": "pao hot dog maxpaes",
        "musarela": "queijo mussarela",
        "muçarela": "queijo mussarela", 
        "mussarela": "queijo mussarela",
        "presunto": "presunto fatiado",
        # Biscoitos e bolachas
        "creme crack": "bolacha cream cracker",
        "cream crack": "bolacha cream cracker",
        "cracker": "bolacha cream cracker",
        # Biscoitos - PADRÃO AMORI RECHEADO
        "biscoito chocolate": "biscoito recheado amori chocolate",
        "biscoito de chocolate": "biscoito recheado amori chocolate",
        "biscoito chocolate pequeno": "biscoito recheado amori chocolate pequeno",

        "biscoito de chocolate pequeno": "biscoito recheado amori chocolate pequeno",
        # Cookies - PRIORIZAR BAUDUCCO
        "cookies": "cookies bauducco original",
        "cookie": "cookies bauducco original",
        "biscoito cookies": "cookies bauducco original",
        # Refrigerantes - MELHORADO
        "guarana": "refrig guarana antarctica",
        "coca cola": "refrig coca cola pet",
        "coca-cola": "refrig coca cola pet",
        "coca cola 2 litros": "refrig coca cola pet 2l",
        "coca-cola 2 litros": "refrig coca cola pet 2l",
        "coca cola 2l": "refrig coca cola pet 2l",
        "coca-cola 2l": "refrig coca cola pet 2l",
        "fanta": "refrig fanta",
        "sprite": "refrig sprite",
        # Padaria - NOVO
        "carioquinha": "pao frances",
        "carioquinhas": "pao frances",
        "pao carioquinha": "pao frances",
        "pão carioquinha": "pao frances",
        "pão francês": "pao frances",
        "pao frances": "pao frances",
        # Carnes e hambúrguer - NOVO
        "hamburguer": "hamburguer carne",
        "hamburger": "hamburguer carne",
        "carne de hamburguer": "hamburguer carne moida",
        "carne hamburguer": "hamburguer carne moida",
        "carne de hamburguer": "hamburguer carne moida",
        "carne de hamburguer": "hamburguer carne moida",
        "carne hamburguer": "hamburguer carne moida",
        # Snacks / Salgadinhos
        "batata rufles": "salgadinho batata ruffles",
        "batata ruffles": "salgadinho batata ruffles",
        "rufles": "salgadinho batata ruffles",
        "ruffles": "salgadinho batata ruffles",
        # Limpeza - NOVO (Qboa/Kiboa)
        "qboa": "agua sanitaria",
        "kiboa": "agua sanitaria",
        "qui boa": "agua sanitaria",
        "quiboa": "agua sanitaria",
        # Higiene pessoal - NOVO (Prestobarba)
        "prestobarba": "barbeador aparelho de barbear",
        "presto barba": "barbeador aparelho de barbear",
        "barbeador prestobarba": "barbeador aparelho de barbear",
        "aparelho de barbear": "barbeador aparelho de barbear",
        "escova": "esc dent",
        "escova de dente": "esc dent",
        "refrescou": "suco em po refresco",
        "refresco": "suco em po refresco",
        "pao de saco": "pao de forma",
        "pão de saco": "pao de forma",
        # "pacote de pao" REMOVIDO - cliente quer hot dog ou hamburguer, agente deve perguntar
        "pao para hamburguer": "pao hamburguer",
        "pao de hamburguer": "pao hamburguer",
        "pao para hot dog": "pao hot dog",
        "pao de hot dog": "pao hot dog",
        "pao de cachorro quente": "pao hot dog",
        # Laticínios
        "leite de saco": "leite liquido",
        "leite saco": "leite liquido",
        # Normalização de acentos (banco usa sem acento)
        "açúcar": "acucar cristal",
        "açucar": "acucar cristal",
        "acucar": "acucar cristal",  # SEM ACENTO - priorizar cristal sobre demerara
        "café": "cafe",
        "maçã": "maca",
        "feijão": "feijao",
        # 🔄 NORMALIZAÇÃO DE PLURAL: "cebolas" -> "cebola" (ANTES do boost de categoria)
        "cebolas": "cebola",
        "tomates": "tomate",
        "batatas": "batata",
        "limoes": "limao",
        "limões": "limao",
        "abacaxis": "abacaxi",
        "laranjas": "laranja",
        "bananas": "banana",
        "maçãs": "maca",
        "macas": "maca",
        # Café - PRIORIZAR TRADICIONAL sobre descafeinado
        "cafe pilao": "cafe pilao tradicional 500g",
        "pilao": "cafe pilao tradicional 500g",
        "cafe melitta": "cafe melitta tradicional",
        "melitta": "cafe melitta tradicional",
        "cafe 3 coracoes": "cafe 3 coracoes tradicional",
        "3 coracoes": "cafe 3 coracoes tradicional",
        # Cervejas - Corrigido para formato do banco (LT = lata, LN = long neck, GRF = garrafa)
        "cerveja": "cerveja lt 350ml",
        "cerveja lata": "cerveja lt 350ml",
        "cerveja latinha": "cerveja lt 350ml",
        "latinha cerveja": "cerveja lt 350ml",
        "latinha de cerveja": "cerveja lt 350ml",
        "cerveja garrafa": "cerveja grf 600ml",
        "cervejas": "cerveja lt 350ml",
        # Long neck (várias grafias)
        "long neck": "cerveja ln 330ml",
        "longneck": "cerveja ln 330ml",
        "longneque": "cerveja ln 330ml",
        "long neque": "cerveja ln 330ml",
        "cerveja long neck": "cerveja ln 330ml",
        # Marcas específicas
        "skol": "cerveja skol lt",
        "brahma": "cerveja brahma chopp lt",
        "antartica": "cerveja antarctica lt",
        "heineken": "cerveja heineken lt",
        "budweiser": "cerveja budweiser lt",
        "amstel": "cerveja amstel lt",
        "bohemia": "cerveja bohemia lt",
        # Marcas locais / Correções específicas
        # A remoção de acentos é GENÉRICA (linha 236). Aqui só expandimos marcas abreviadas.
        "arroz vo": "arroz vo olimpio",  # "vo" → marca completa
        "vo olimpio": "vo olimpio",  # Manter se já vier completo
        # Nescal = Nescau Líquido (caixinha pronta pra beber)
        "nescal": "achoc liq nescau",
        "nescau": "achoc liq nescau",  # Se pedir "nescau" solto, priorizar caixinha
        "nescau po": "achoc po nescau",  # Se especificar pó, retorna pó
        "nescau lata": "achoc po nescau",  # Lata = pó
    }
    
    
    # ---------------------------------------------------------
    # 🆕 REGRA GLOBAL: REMOVER ACENTOS (Pedido do usuário)
    # O banco de dados não tem acentos (ex: "vô" -> "vo", "maçã" -> "maca").
    # Removemos logo no início para garantir match FTS/Vetorial.
    # ---------------------------------------------------------
    import unicodedata
    def remove_accents(input_str):
        nfkd_form = unicodedata.normalize('NFKD', input_str)
        return "".join([c for c in nfkd_form if not unicodedata.combining(c)])

    query = remove_accents(query)
    query_lower = query.lower().strip()
    enhanced_query = query
    
    # Primeiro, aplicar traduções de termos (ORDENAR por tamanho decrescente para pegar matches maiores primeiro)
    sorted_translations = sorted(TERM_TRANSLATIONS.items(), key=lambda x: len(x[0]), reverse=True)
    for term, abbreviation in sorted_translations:
        # Remover acentos também das chaves do dicionário para garantir match
        term_clean = remove_accents(term)
        if term_clean in query_lower:
            # SUBSTITUIÇÃO PARCIAL: Preserva outras palavras (ex: "Coca Cola Zero" -> "refrig coca cola pet Zero")
            # Usa replace para trocar apenas o termo encontrado
            enhanced_query = enhanced_query.lower().replace(term_clean, abbreviation)
            # Atualiza query principal também para refletir a mudança
            query = enhanced_query
            logger.info(f"🔄 [TRADUÇÃO PARCIAL] '{term_clean}' → '{abbreviation}' | Result: '{enhanced_query}'")
            # Não dar break, permitir outras substituições se não conflitarem (mas cuidado com loop). 
            # Como ordenamos por tamanho, os maiores já foram.
            # Mas para segurança, melhor parar após a primeira substituição principal para evitar salada.
            break
    
    # Se a busca é por um produto hortifruti, adiciona contexto para melhorar a relevância
    # Se a busca é por um produto hortifruti, adiciona contexto para melhorar a relevância
    # MAS: Se a busca contém termos de produtos processados, NÃO aplicar boost de hortifruti
    PROCESSED_TERMS = [
        "doce", "suco", "molho", "extrato", "polpa", "geleia", "compota", "refresco", "refrescou",
        "rufles", "ruffles", "batata palha", "batata chips", "chips", "salgadinho", "snack",
        "cheetos", "fandangos", "doritos", "pringles", "stax", "baconzitos", "cebolitos"
    ]
    is_processed = any(term in query_lower for term in PROCESSED_TERMS)
    
    if not is_processed:
        import re
        query_to_check = query.lower()
        for keyword in HORTIFRUTI_KEYWORDS:
            # Usar regex para buscar palavra exata (evita "maca" em "macarrao")
            if re.search(r'\b' + re.escape(keyword) + r'\b', query_to_check):
                # Adiciona contexto de categoria para melhorar a similaridade
                if keyword in ["frango"]:
                    enhanced_query = f"{query} açougue carnes abatido resfriado"
                elif keyword in ["carne", "peixe"]:
                    enhanced_query = f"{query} açougue carnes"
                elif keyword in ["ovo", "leite", "queijo", "manteiga", "iogurte"]:
                    enhanced_query = f"{query} laticínios"
                else:
                    enhanced_query = f"{query} hortifruti legumes verduras frutas"
                logger.info(f"🎯 [BOOST] Query melhorada: '{enhanced_query}'")
                break
    else:
        logger.info(f"⏭️ [BOOST SKIP] Produto processado detectado, pulando boost hortifruti")
    
    logger.info(f"🔍 [VECTOR SEARCH] Buscando: '{query}'" + (f" → '{enhanced_query}'" if enhanced_query != query else ""))
    
    try:
        # 1. Gerar embedding da query (com boost se aplicável)
        query_embedding = _generate_embedding(enhanced_query)
        # 2. BUSCA HÍBRIDA usando função PostgreSQL (FTS + Vetorial com RRF)
        with psycopg2.connect(conn_str) as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                # Converter embedding para string no formato pgvector
                embedding_str = f"[{','.join(map(str, query_embedding))}]"
                
                # 🔥 BUSCA HÍBRIDA V2: FTS + Vetorial + Boost para HORTI-FRUTI/FRIGORIFICO
                # Usa RRF (Reciprocal Rank Fusion) para combinar rankings
                # - full_text_weight: peso da busca por texto
                # - semantic_weight: peso da busca vetorial
                # - setor_boost: +0.5 para HORTI-FRUTI e FRIGORIFICO
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
                
                logger.info(f"🔀 [HYBRID SEARCH] Query: '{query}' → '{enhanced_query}'")
                
                cur.execute(sql, (enhanced_query, embedding_str, limit))
                results = cur.fetchall()
                
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
                
                # 🔄 RETRY AUTOMÁTICO - DESABILITADO POR PERFORMANCE (Lentidão de 10s+)
                # O loop palavra-por-palavra fazia múltiplas chamadas de embedding sequenciais.
                # A busca híbrida inicial + FlashRank deve ser suficiente.
                pass
                # if results and results[0].get("similarity", 0) < MIN_SCORE_THRESHOLD:
                #    ... (Código removido para otimização) ...
                
                if not results:
                    return "Nenhum produto encontrado com esse termo."

                # FlashRank REMOVIDO - O Analista (LLM) decide o melhor produto
                # GENERIC BOOST REMOVIDO - O Analista (LLM) decide o melhor produto
                # Os resultados já vêm ordenados pela busca híbrida (FTS + Vetorial)

                # 3. Processar e formatar resultados
                return _format_results(results)
    
    except Exception as e:
        logger.error(f"❌ Erro na busca vetorial: {e}")
        return f"Erro ao buscar no banco vetorial: {str(e)}"


def _extract_ean_and_name(result: dict) -> tuple[str, str]:
    """
    Extrai EAN e nome do produto do resultado.
    O n8n salva os dados em 'text' (conteúdo) e 'metadata' (JSON).
    """
    text = result.get("text", "")
    metadata = result.get("metadata", {})
    
    # Tentar extrair do metadata primeiro (mais confiável)
    if isinstance(metadata, str):
        try:
            metadata = json.loads(metadata)
        except:
            metadata = {}
    
    ean = ""
    nome = ""
    
    # Buscar EAN no metadata ou no texto
    if metadata:
        ean = str(metadata.get("codigo_ean", metadata.get("ean", "")))
        nome = metadata.get("produto", metadata.get("nome", ""))
    
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
