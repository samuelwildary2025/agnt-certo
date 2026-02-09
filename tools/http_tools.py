"""
Ferramentas HTTP para interação com a API do Supermercado
"""
import requests
import json
from typing import Dict, Any, Optional
from config.settings import settings
from config.logger import setup_logger
from .db_vector_search import search_products_vector

logger = setup_logger(__name__)


def get_auth_headers() -> Dict[str, str]:
    """Retorna os headers de autenticação para as requisições"""
    token = settings.supermercado_auth_token or ""
    
    # Fallback: Tentar ler TOKEN_SUPERMERCADO direto do environment caso o settings esteja vazio
    # (Caso o usuário tenha nomeado diferente no .env)
    if not token or len(token) < 10:
        import os
        from dotenv import load_dotenv
        
        # FORÇAR recarregamento do .env para pegar mudanças sem reiniciar servidor
        load_dotenv(override=True)
        
        token_env = os.getenv("TOKEN_SUPERMERCADO", "")
        if token_env:
            logger.info("⚠️ Usando TOKEN_SUPERMERCADO do env (fallback reload)")
            token = token_env
        else:
             # Tentar SUPERMERCADO_AUTH_TOKEN direto também
            token = os.getenv("SUPERMERCADO_AUTH_TOKEN", token)

    # Garantir que o token tenha o prefixo Bearer se não tiver
    if token and not token.strip().lower().startswith("bearer"):
        token = f"Bearer {token.strip()}"
    
    # DEBUG: Verificar formato do token (mascarado)
    safe_token = f"{token[:15]}...{token[-5:]}" if len(token) > 20 else "CURTO/VAZIO"
    logger.info(f"🔑 Auth Header gerado: {safe_token}")
        
    return {
        "Authorization": token,
        "Accept": "application/json",
        "Content-Type": "application/json"
    }


def estoque(url: str) -> str:
    """
    Consulta o estoque e preço de produtos no sistema do supermercado.
    
    Args:
        url: URL completa para consulta (ex: .../api/produtos/consulta?nome=arroz)
    
    Returns:
        JSON string com informações do produto ou mensagem de erro
    """
    logger.info(f"Consultando estoque: {url}")
    
    try:
        response = requests.get(
            url,
            headers=get_auth_headers(),
            timeout=10
        )
        response.raise_for_status()
        
        data = response.json()
        
        # OTIMIZAÇÃO DE TOKENS: Filtrar apenas campos essenciais
        # A API retorna muitos dados inúteis (impostos, ncm, ids internos)
        # que gastam tokens desnecessariamente.
        def _filter_product(prod: Dict[str, Any]) -> Dict[str, Any]:
            keys_to_keep = [
                "id", "id_loja", "produto", "nome", "descricao", 
                "preco", "preco_venda", "valor", "valor_unitario",
                "estoque", "quantidade", "saldo", "disponivel"
            ]
            clean = {}
            for k, v in prod.items():
                if k.lower() in keys_to_keep or any(x in k.lower() for x in ["preco", "valor", "estoque"]):
                    # Ignora campos de imposto/fiscal mesmo se tiver palavras chave
                    if any(x in k.lower() for x in ["trib", "ncm", "fiscal", "custo", "margem"]):
                        continue
                    clean[k] = v
            return clean

        if isinstance(data, list):
            filtered_data = [_filter_product(p) for p in data]
        elif isinstance(data, dict):
            filtered_data = _filter_product(data)
        else:
            filtered_data = data
            
        logger.info(f"Estoque consultado com sucesso: {len(data) if isinstance(data, list) else 1} produto(s)")
        
        return json.dumps(filtered_data, indent=2, ensure_ascii=False)
    
    except requests.exceptions.Timeout:
        error_msg = "Erro: Timeout ao consultar estoque. Tente novamente."
        logger.error(error_msg)
        return error_msg
    
    except requests.exceptions.HTTPError as e:
        error_msg = f"Erro HTTP ao consultar estoque: {e.response.status_code} - {e.response.text}"
        logger.error(error_msg)
        return error_msg
    
    except requests.exceptions.RequestException as e:
        error_msg = f"Erro ao consultar estoque: {str(e)}"
        logger.error(error_msg)
        return error_msg
    
    except json.JSONDecodeError:
        error_msg = "Erro: Resposta da API não é um JSON válido."
        logger.error(error_msg)
        return error_msg


def pedidos(json_body: str) -> str:
    """
    Envia um pedido finalizado para o painel dos funcionários (dashboard).
    
    Args:
        json_body: JSON string com os detalhes do pedido
                   Exemplo: '{"cliente": "João", "itens": [{"produto": "Arroz", "quantidade": 1}]}'
    
    Returns:
        Mensagem de sucesso com resposta do servidor ou mensagem de erro
    """
    # Remove trailing slashed from base and from endpoint to ensure correct path
    base = settings.supermercado_base_url.rstrip("/")
    url = f"{base}/pedidos/"  # Barra final necessária para FastAPI
    logger.info(f"Enviando pedido para: {url}")
    
    # DEBUG: Log token being used (only first/last 4 chars for security)
    token = settings.supermercado_auth_token or ""
    token_preview = f"{token[:12]}...{token[-4:]}" if len(token) > 16 else token
    logger.info(f"🔑 Token usado: {token_preview}")
    
    try:
        # Validar JSON
        data = json.loads(json_body)
        logger.debug(f"Dados do pedido: {data}")
        
        response = requests.post(
            url,
            headers=get_auth_headers(),
            json=data,
            timeout=10
        )
        response.raise_for_status()
        
        result = response.json()
        success_msg = f"✅ Pedido enviado com sucesso!\n\nResposta do servidor:\n{json.dumps(result, indent=2, ensure_ascii=False)}"
        logger.info("Pedido enviado com sucesso")
        
        return success_msg
    
    except json.JSONDecodeError:
        error_msg = "Erro: O corpo da requisição não é um JSON válido."
        logger.error(error_msg)
        return error_msg
    
    except requests.exceptions.Timeout:
        error_msg = "Erro: Timeout ao enviar pedido. Tente novamente."
        logger.error(error_msg)
        return error_msg
    
    except requests.exceptions.HTTPError as e:
        error_msg = f"Erro HTTP ao enviar pedido: {e.response.status_code} - {e.response.text}"
        logger.error(error_msg)
        return error_msg
    
    except requests.exceptions.RequestException as e:
        error_msg = f"Erro ao enviar pedido: {str(e)}"
        logger.error(error_msg)
        return error_msg


def alterar(telefone: str, json_body: str) -> str:
    """
    Atualiza um pedido existente. 
    LÓGICA 'ADICIONAR': Busca o pedido atual e ADICIONA os novos itens que vieram no json_body,
    a menos que a instrução explícita seja de substituir.
    
    Args:
        telefone: Telefone do cliente
        json_body: JSON com "itens" novos. 
                   Ex: '{"itens": [{"produto": "Coca", "quantidade": 1}]}'
    """
    # Remove caracteres não numéricos do telefone
    telefone_limpo = "".join(filter(str.isdigit, telefone))
    base_url = f"{settings.supermercado_base_url}/pedidos/telefone/{telefone_limpo}"
    
    logger.info(f"Atualizando pedido para telefone: {telefone_limpo}")
    
    try:
        data_update = json.loads(json_body)
        novos_itens = data_update.get("itens", [])
        
        # 1. BUSCAR PEDIDO ATUAL (GET)
        # Precisamos da lista atual para não apagar o que já existe
        try:
            get_response = requests.get(base_url, headers=get_auth_headers(), timeout=10)
            get_response.raise_for_status()
            pedido_atual = get_response.json()
            
            # Extrair itens atuais. Backend pode retornar 'itens' ou 'items'
            itens_atuais = pedido_atual.get("itens", pedido_atual.get("items", []))
            
            # Se pedido não existe ou lista vazia, apenas ignoramos o merge
            if not isinstance(itens_atuais, list):
                itens_atuais = []
                
        except Exception as e:
            logger.warning(f"⚠️ Não foi possível recuperar pedido atual para merge: {e}. Criando novo ou sobrescrevendo.")
            itens_atuais = []

        # 2. MERGE (APPEND)
        # O cliente reclamou que 'atualizar' apagava tudo. Então vamos adicionar.
        # TODO: Se quisermos remover, precisamos de uma lógica mais complexa (ex: qtd=-1)
        # Por enquanto, assumimos que o LLM manda apenas o que é para ADICIONAR.
        
        itens_finais = itens_atuais + novos_itens
        
        # Atualizar o payload apenas com a lista mergeada
        data_update["itens"] = itens_finais
        
        # 3. ENVIAR ATUALIZAÇÃO (PUT)
        response = requests.put(
            base_url,
            headers=get_auth_headers(),
            json=data_update,
            timeout=10
        )
        response.raise_for_status()
        
        result = response.json()
        
        # Montar resumo para o LLM
        total_items = len(itens_finais)
        success_msg = (f"✅ Pedido atualizado! {len(novos_itens)} itens adicionados.\n"
                       f"Total de itens agora: {total_items}.\n"
                       f"Resposta Servidor: {json.dumps(result, indent=2, ensure_ascii=False)}")
        
        logger.info(f"Pedido atualizado com sucesso. Itens: {len(itens_atuais)} -> {total_items}")
        
        return success_msg
    
    except json.JSONDecodeError:
        error_msg = "Erro: O corpo da requisição não é um JSON válido."
        logger.error(error_msg)
        return error_msg
        error_msg = f"Erro ao atualizar pedido: {str(e)}"
        logger.error(error_msg)
        return error_msg


def overwrite_order(telefone: str, json_body: str) -> str:
    """
    Sobrescreve o pedido existente com os dados fornecidos (PUT direto).
    Usado quando o agente possui o estado completo do pedido (ex: via Redis).
    
    Args:
        telefone: Telefone do cliente
        json_body: JSON com "itens" completos.
    """
    # Remove caracteres não numéricos do telefone
    telefone_limpo = "".join(filter(str.isdigit, telefone))
    base_url = f"{settings.supermercado_base_url}/pedidos/telefone/{telefone_limpo}"
    
    logger.info(f"🔄 Sobrescrevendo pedido para telefone: {telefone_limpo} (Full Sync)")
    
    try:
        # Validar JSON
        data = json.loads(json_body)
        
        # ENVIAR ATUALIZAÇÃO (PUT)
        response = requests.put(
            base_url,
            headers=get_auth_headers(),
            json=data,
            timeout=10
        )
        response.raise_for_status()
        
        result = response.json()
        itens = data.get("itens", [])
        
        success_msg = (f"✅ Pedido sincronizado! Total de itens: {len(itens)}.\n"
                       f"Resposta Servidor: {json.dumps(result, indent=2, ensure_ascii=False)}")
        
        logger.info(f"Pedido sobrescrito com sucesso. Total itens: {len(itens)}")
        return success_msg
        
    except json.JSONDecodeError:
        return "Erro: JSON inválido para overwrite."
    except Exception as e:
        error_msg = f"Erro ao sobrescrever pedido: {str(e)}"
        logger.error(error_msg)
        return error_msg


def ean_lookup(query: str) -> str:
    """
    Busca informações/EAN do produto via busca vetorial (pgvector + OpenAI embeddings).

    Args:
        query: Texto com o nome/descrição do produto ou entrada de chat.

    Returns:
        String com lista de EANs encontrados ou mensagem de erro.
    """
    logger.info(f"🔍 [VECTOR] Consultando pgvector: query='{query}'")
    return search_products_vector(query)



def estoque_preco(ean: str) -> str:
    """
    Consulta preço e disponibilidade pelo EAN.

    Monta a URL completa concatenando o EAN ao final de settings.estoque_ean_base_url.
    Exemplo: {base}/7891149103300
    
    MELHORIAS:
    - Retry automático com backoff exponencial (3 tentativas)
    - Timeouts progressivos para lidar com API lenta

    Args:
        ean: Código EAN do produto (apenas dígitos).

    Returns:
        JSON string com informações do produto ou mensagem de erro amigável.
    """
    import time
    
    base = (settings.estoque_ean_base_url or "").strip().rstrip("/")
    if not base:
        msg = "Erro: ESTOQUE_EAN_BASE_URL não configurado no .env"
        logger.error(msg)
        return msg

    # CIRCUIT BREAKER CHECK
    from tools.redis_tools import check_circuit_open, report_failure, report_success
    from tools.redis_tools import get_redis_client
    SERVICE_NAME = "estoque_api"
    CACHE_TTL = 21600
    
    # manter apenas dígitos no EAN
    ean_digits = "".join(ch for ch in ean if ch.isdigit())
    if not ean_digits:
        msg = "Erro: EAN inválido. Informe apenas números."
        logger.error(msg)
        return msg

    cache_key = f"estoque_preco_cache:{ean_digits}"

    if check_circuit_open(SERVICE_NAME):
        client = get_redis_client()
        if client is not None:
            try:
                cached = client.get(cache_key)
                if cached:
                    logger.warning(f"Circuit Breaker ativo; retornando cache para EAN {ean_digits}")
                    return cached if isinstance(cached, str) else str(cached)
            except Exception:
                pass
        msg = "⚠️ O sistema de estoque está instável no momento. Tente novamente em alguns minutos."
        logger.warning(f"Circuit Breaker impediu chamada para {ean_digits}")
        return msg

    url = f"{base}/{ean_digits}"
    
    headers = {
        "Accept": "application/json",
    }
    
    # RETRY CONFIG
    MAX_RETRIES = 3
    TIMEOUTS = [10, 15, 20]  # Timeouts aumentados para evitar "Problema Técnico" em redes lentas
    
    last_error = None
    
    for attempt in range(MAX_RETRIES):
        timeout = TIMEOUTS[min(attempt, len(TIMEOUTS) - 1)]
        
        try:
            if attempt > 0:
                logger.info(f"🔄 Retry #{attempt + 1} para EAN {ean_digits} (timeout: {timeout}s)")
            else:
                logger.info(f"Consultando estoque_preco por EAN: {url}")
            
            resp = requests.get(url, headers=headers, timeout=timeout)
            resp.raise_for_status()

            # SUCESSO DO CIRCUIT BREAKER
            report_success(SERVICE_NAME)

            # resposta esperada: lista de objetos
            try:
                items = resp.json()
            except json.JSONDecodeError:
                txt = resp.text
                logger.warning("Resposta não é JSON válido; retornando texto bruto")
                return txt

            # Se vier um único objeto, normalizar para lista
            items = items if isinstance(items, list) else ([items] if isinstance(items, dict) else [])

            # Heurística de extração de preço
            PRICE_KEYS = (
                "vl_produto",
                "vl_produto_normal",
                "preco",
                "preco_venda",
                "valor",
                "valor_unitario",
                "preco_unitario",
                "atacadoPreco",
            )

            # Chaves de quantidade em ordem de prioridade
            STOCK_QTY_KEYS = [
                "qtd_produto",  # Chave principal do sistema
                # "qtd_movimentacao", # REMOVIDO: Cliente confirmou que este campo não serve para estoque (gera falso positivo)
                "estoque", "qtd", "qtde", "qtd_estoque", "quantidade", "quantidade_disponivel",
                "quantidadeDisponivel", "qtdDisponivel", "qtdEstoque", "estoqueAtual", "saldo",
                "qty", "quantity", "stock", "amount"
            ]

            # Possíveis indicadores de disponibilidade
            STATUS_KEYS = ("situacao", "situacaoEstoque", "status", "statusEstoque")

            def _parse_float(val) -> Optional[float]:
                try:
                    s = str(val).strip()
                    if not s:
                        return None
                    # aceita formato brasileiro
                    s = s.replace(".", "").replace(",", ".") if s.count(",") == 1 and s.count(".") > 1 else s.replace(",", ".")
                    return float(s)
                except Exception:
                    return None

            def _has_positive_qty(d: Dict[str, Any]) -> bool:
                # Tenta encontrar qualquer chave que tenha valor > 0
                for k in STOCK_QTY_KEYS:
                    if k in d:
                        v = d.get(k)
                        try:
                            n = float(str(v).replace(",", "."))
                            if n > 0:
                                return True
                        except Exception:
                            # ignore não numérico
                            pass
                return False

            def _extract_price(d: Dict[str, Any]) -> Optional[float]:
                best_price = None
                for k in PRICE_KEYS:
                    if k in d:
                        val = _parse_float(d.get(k))
                        if val is not None:
                            if val > 0:
                                return val
                            elif best_price is None:
                                best_price = val
                return best_price

            def _extract_qty(d: Dict[str, Any]) -> Optional[float]:
                for k in STOCK_QTY_KEYS:
                    if k in d:
                        val = _parse_float(d.get(k))
                        if val is not None:
                            return val
                return None

            def _is_available(d: Dict[str, Any]) -> bool:
                # 1. Verificar se está ativo (se a flag existir)
                is_active = d.get("ativo", True)
                if not is_active:
                    logger.debug(f"Item filtrado: ativo=False")
                    return False

                # 2. Verificar Estoque
                qty = _extract_qty(d)
                
                # Categorias que NÃO verificam estoque (produção própria ou pesagem)
                # PADARIA: produtos feitos na hora, não têm controle de quantidade
                # FRIGORIFICO/AÇOUGUE: vendem antes de dar entrada na nota
                # HORTI/LEGUMES: idem, produção variável
                cat1 = str(d.get("classificacao01", "") or "")
                cat2 = str(d.get("classificacao02", "") or "")
                cat3 = str(d.get("classificacao03", "") or "")
                cat = f"{cat1} {cat2} {cat3}".upper()
                name_upper = str(d.get("produto") or d.get("nome") or "").upper()
                
                # Lista expandida de termos que IGNORAM estoque
                keywords_ignore_stock = [
                    "PADARIA", "FRIGORIFICO", "HORTI", "AÇOUGUE", "ACOUGUE", 
                    "LEGUMES", "VERDURAS", "AVES", "CARNES", "FLV", "FRUTA",
                    "FRANGO", "LINGUICA", "RESFRIADO", "CONGELADO", "BIFE", "MOIDA", "PICADINHO"
                ]
                
                ignora_estoque = any(x in cat for x in keywords_ignore_stock) or \
                                 any(x in name_upper for x in keywords_ignore_stock)
                
                if ignora_estoque:
                    # Regra de Exceção: Setor INDUSTRIAL (ex: Padaria Industrial)
                    # Produtos industrializados/embalados DEVEM respeitar o estoque do sistema
                    # MAS se for FRIGORIFICO ou CARNE, ignora sempre (regra do cliente)
                    is_meat = any(x in cat for x in ["FRIGORIFICO", "AVES", "CARNES"]) or \
                              any(x in name_upper for x in ["FRANGO", "CARNE", "LINGUICA", "BIFE"])
                              
                    if "INDUSTRIAL" in cat and not is_meat:
                        logger.debug(f"Item de {cat}: Setor Industrial detectado, forçando verificação de estoque.")
                        # Continua para o check de quantidade lá embaixo...
                    else:
                        logger.debug(f"Item de {cat}/{name_upper}: ignorando verificação de estoque (ativo={is_active})")
                        return True
                
                # REGRAS ESPECIAIS DE PESAGEM (KG) E PLU (Códigos curtos)
                ean_str = str(d.get("cod_barra") or d.get("id") or "").strip()
                
                is_weighted = "KG" in name_upper.split() or name_upper.endswith("KG")
                is_plu = len(ean_str) > 0 and len(ean_str) <= 5 and ean_str.isdigit()
                
                if is_weighted or is_plu:
                     logger.debug(f"Item PESADO/PLU detectado ({name_upper} [{ean_str}]): ignorando verificação de estoque.")
                     return True

                # Para os demais (Mercearia, Bebidas, INDUSTRIAL, etc), estoque deve ser POSITIVO
                if qty is not None and qty > 0:
                    return True
                
                # Se chegou aqui, ou é 0, ou é negativo em categoria que não pode
                logger.debug(f"Item filtrado: quantidade={qty} (Categoria: {cat})")
                return False

            # [OTIMIZAÇÃO] Filtro estrito para saída
            sanitized: list[Dict[str, Any]] = []
            for it in items:
                if not isinstance(it, dict):
                    continue
                if not _is_available(it):
                    continue  # manter apenas itens com estoque/disponibilidade

                # Cria dict limpo apenas com campos úteis para o agente
                clean: Dict[str, Any] = {}

                # ID da Loja e Identificadores
                if "id_loja" in it:
                    clean["id_loja"] = it["id_loja"]
                if "id" in it:
                    clean["id"] = it["id"]

                produto_nome = it.get("produto") or it.get("nome") or it.get("descricao")
                if produto_nome:
                    clean["produto"] = produto_nome

                price = _extract_price(it)
                if price is not None:
                    clean["preco"] = price
                    clean["vl_produto"] = price

                preco_normal = _parse_float(it.get("vl_produto_normal"))
                if preco_normal is not None:
                    clean["vl_produto_normal"] = preco_normal

                qtd_prod = _parse_float(it.get("qtd_produto"))
                if qtd_prod is not None:
                    clean["qtd_produto"] = qtd_prod

                for k in ["dt_cadastro", "classificacao01", "classificacao02", "classificacao03", "fracionado", "ativo", "fracionamento", "emb"]:
                    if k in it:
                        clean[k] = it.get(k)

                clean["disponibilidade"] = True

                sanitized.append(clean)

            logger.info(f"EAN {ean_digits}: {len(sanitized)} item(s) disponíveis após filtragem")

            out = json.dumps(sanitized, indent=2, ensure_ascii=False)
            client = get_redis_client()
            if client is not None:
                try:
                    client.set(cache_key, out, ex=CACHE_TTL)
                except Exception:
                    pass
            return out

        except requests.exceptions.Timeout:
            last_error = f"Timeout (tentativa {attempt + 1}/{MAX_RETRIES})"
            logger.warning(f"⏱️ {last_error}")
            
            # FALHA DO CIRCUIT BREAKER (TIMEOUT)
            report_failure(SERVICE_NAME)
            
            if attempt < MAX_RETRIES - 1:
                time.sleep(0.5)  # Pequena pausa antes de retry
                continue
        except requests.exceptions.HTTPError as e:
            status = getattr(e.response, "status_code", "?")
            body = getattr(e.response, "text", "")
            msg = f"Erro HTTP ao consultar EAN: {status} - {body}"
            logger.error(msg)
            
            # FALHA DO CIRCUIT BREAKER (Erro Servidor 500+)
            if str(status).startswith("5"):
                report_failure(SERVICE_NAME)
            
            return msg
        except requests.exceptions.RequestException as e:
            msg = f"Erro ao consultar EAN: {str(e)}"
            logger.error(msg)
            report_failure(SERVICE_NAME)
            return msg
    
    client = get_redis_client()
    if client is not None:
        try:
            cached = client.get(cache_key)
            if cached:
                logger.warning(f"Falha consultando EAN {ean_digits}; retornando cache")
                return cached if isinstance(cached, str) else str(cached)
        except Exception:
            pass

    msg = f"Erro: API lenta. Não foi possível consultar EAN após {MAX_RETRIES} tentativas."
    logger.error(msg)
    return msg


# ============================================
# ANTIGA BUSCA EM LOTE (Descontinuada em favor do Sub-Agente)
# ============================================

def busca_lote_produtos(produtos: list[str]) -> str:
    """
    OBSOLETO: Esta função foi substituída pelo `search_specialist_tool` (Sub-Agente).
    Mantida apenas como stub para evitar quebras se algo antigo chamar.
    """
    from tools.search_agent import search_specialist_tool
    
    # Redireciona para a nova tool do sub-agente
    queries_str = ",".join(produtos)
    return search_specialist_tool(queries_str)




def consultar_encarte() -> str:
    """
    Consulta o encarte atual do supermercado.
    Suporta múltiplos encartes via campo active_encartes_urls.
    
    Returns:
        JSON string com a URL (ou lista de URLs) do encarte ou mensagem de erro.
    """
    # Remove trailing slash from base to ensure correct path
    base = settings.supermercado_base_url.rstrip("/")
    url = f"{base}/encarte/"
    
    logger.info(f"Consultando encarte: {url}")
    
    try:
        response = requests.get(
            url,
            headers=get_auth_headers(),
            timeout=10
        )
        response.raise_for_status()
        
        data = response.json()
        logger.info(f"Encarte obtido com sucesso: {data}")
        
        domain = "https://app.aimerc.com.br"
        
        def _fix_url(u: str) -> str:
            if not u: return u
            if u.startswith("/"):
                u = f"{domain}{u}"
            elif "supermercadoqueiroz.com.br" in u:
                u = u.replace("https://supermercadoqueiroz.com.br", domain).replace("http://supermercadoqueiroz.com.br", domain)
            return u

        # 1. Tentar processar lista de encartes ativos (Novo comportamento)
        active_urls = data.get("active_encartes_urls")
        if isinstance(active_urls, list):
            data["active_encartes_urls"] = [_fix_url(u) for u in active_urls if u]
            # Se tivermos a lista, atualizamos o encarte_url legado com o primeiro da lista para compatibilidade
            if data["active_encartes_urls"]:
                data["encarte_url"] = data["active_encartes_urls"][0]
            else:
                data["encarte_url"] = ""
        
        # 2. Fallback/Processamento fixo do campo antigo se o novo não existir ou não for lista
        else:
            encarte_url = data.get("encarte_url", "")
            if encarte_url:
                data["encarte_url"] = _fix_url(encarte_url)
                # Garante que active_encartes_urls também exista como lista de um item
                data["active_encartes_urls"] = [data["encarte_url"]]
            else:
                data["encarte_url"] = ""
                data["active_encartes_urls"] = []
            
        return json.dumps(data, indent=2, ensure_ascii=False)
        
    except requests.exceptions.Timeout:
        error_msg = "Erro: Timeout ao consultar encarte. Tente novamente."
        logger.error(error_msg)
        return error_msg
    
    except requests.exceptions.HTTPError as e:
        error_msg = f"Erro HTTP ao consultar encarte: {e.response.status_code} - {e.response.text}"
        logger.error(error_msg)
        return error_msg
    
    except requests.exceptions.RequestException as e:
        error_msg = f"Erro ao consultar encarte: {str(e)}"
        logger.error(error_msg)
        return error_msg
    
    except json.JSONDecodeError:
        error_msg = "Erro: Resposta do encarte não é um JSON válido."
        logger.error(error_msg)
        return error_msg
