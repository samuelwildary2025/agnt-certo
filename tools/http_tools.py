"""
Ferramentas HTTP para interação com a API do Supermercado
"""
import requests
import json
from typing import Dict, Any
from config.settings import settings
from config.logger import setup_logger

logger = setup_logger(__name__)


def get_auth_headers() -> Dict[str, str]:
    """Retorna os headers de autenticação para as requisições"""
    return {
        "Authorization": settings.supermercado_auth_token,
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
        logger.info(f"Estoque consultado com sucesso: {len(data) if isinstance(data, list) else 1} produto(s)")
        
        return json.dumps(data, indent=2, ensure_ascii=False)
    
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
    url = f"{settings.supermercado_base_url}/pedidos/"
    logger.info(f"Enviando pedido para: {url}")
    
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
    Atualiza um pedido existente no painel dos funcionários (dashboard).
    
    Args:
        telefone: Telefone do cliente para identificar o pedido
        json_body: JSON string com os dados a serem atualizados
    
    Returns:
        Mensagem de sucesso com resposta do servidor ou mensagem de erro
    """
    # Remove caracteres não numéricos do telefone
    telefone_limpo = "".join(filter(str.isdigit, telefone))
    url = f"{settings.supermercado_base_url}/pedidos/telefone/{telefone_limpo}"
    
    logger.info(f"Atualizando pedido para telefone: {telefone_limpo}")
    
    try:
        # Validar JSON
        data = json.loads(json_body)
        logger.debug(f"Dados de atualização: {data}")
        
        response = requests.put(
            url,
            headers=get_auth_headers(),
            json=data,
            timeout=10
        )
        response.raise_for_status()
        
        result = response.json()
        success_msg = f"✅ Pedido atualizado com sucesso!\n\nResposta do servidor:\n{json.dumps(result, indent=2, ensure_ascii=False)}"
        logger.info("Pedido atualizado com sucesso")
        
        return success_msg
    
    except json.JSONDecodeError:
        error_msg = "Erro: O corpo da requisição não é um JSON válido."
        logger.error(error_msg)
        return error_msg


def ean_lookup(query: str) -> str:
    """
    Busca informações/EAN do produto mencionado via Supabase Functions (smart-responder).

    Envia POST para settings.smart_responder_url com header Authorization Bearer e body {"query": query}.

    Args:
        query: Texto com o nome/descrição do produto ou entrada de chat.

    Returns:
        String com JSON de resposta ou mensagem de erro amigável.
    """
    url = (settings.smart_responder_url or "").strip()
    # Prefer new envs; fall back to legacy token
    auth_token = (settings.smart_responder_auth or settings.smart_responder_token or "").strip()
    api_key = (settings.smart_responder_apikey or "").strip()

    if not url or not auth_token:
        msg = "Erro: SMART_RESPONDER_URL/AUTH não configurados no .env"
        logger.error(msg)
        return msg

    # Remover crases/backticks caso estejam coladas ao URL
    url = url.replace("`", "")

    # Normalizar Authorization: aceitar com ou sem prefixo 'Bearer '
    auth_value = auth_token if auth_token.lower().startswith("bearer ") else f"Bearer {auth_token}"
    headers = {
        "Authorization": auth_value,
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    # Incluir apikey somente se explicitamente configurada
    if api_key:
        headers["apikey"] = api_key

    payload = {"query": query}
    logger.info(f"Consultando smart-responder: {url} query='{query[:80]}'")

    def _extract_pairs_from_text(text: str):
        import re
        eans = re.findall(r'"codigo_ean"\s*:\s*([0-9]+)', text)
        names = re.findall(r'"produto"\s*:\s*"([^"]+)"', text)
        # Emparelhar por ordem de aparição; não limitar aqui
        pairs = []
        limit = min(len(eans), len(names)) or max(len(eans), len(names))
        for i in range(min(limit, 50)):
            e = eans[i] if i < len(eans) else None
            n = names[i] if i < len(names) else None
            if e or n:
                pairs.append((e, n))
        return pairs

    def _format_summary(pairs):
        if not pairs:
            return None
        lines = ["EANS_ENCONTRADOS:"]
        for idx, (e, n) in enumerate(pairs, 1):
            if e and n:
                lines.append(f"{idx}) {e} - {n}")
            elif e:
                lines.append(f"{idx}) {e}")
            elif n:
                lines.append(f"{idx}) {n}")
        return "\n".join(lines)

    try:
        resp = requests.post(url, headers=headers, json=payload, timeout=15)
        status = resp.status_code
        text = resp.text
        logger.info(f"smart-responder retorno: status={status}")

        # Tentar interpretar como JSON e extrair EAN/nome quando possível
        try:
            data = resp.json()

            # Caminho 1: procurar pares diretamente em campos estruturados
            pairs = []
            def try_obj(d: Dict[str, Any]):
                # EAN pode ser string ou número
                e = None
                for k in ["ean", "ean_code", "codigo_ean", "barcode", "gtin"]:
                    v = d.get(k)
                    if isinstance(v, (str, int)) and str(v).strip():
                        e = str(v).strip()
                        break
                n = None
                for k in ["produto", "product", "name", "nome", "title", "descricao", "description"]:
                    v = d.get(k)
                    if isinstance(v, str) and v.strip():
                        n = v.strip()
                        break
                if e or n:
                    pairs.append((e, n))

            def walk(payload: Any):
                if isinstance(payload, dict):
                    # Primeiro tenta extrair diretamente do objeto
                    try_obj(payload)
                    # Percorre TODOS os campos do dict, não apenas nomes comuns
                    for _, val in payload.items():
                        if isinstance(val, dict):
                            walk(val)
                        elif isinstance(val, list):
                            for it in val:
                                walk(it)
                        elif isinstance(val, str):
                            # Conteúdos string (ex.: campo "content" vindo do Supabase)
                            pairs.extend(_extract_pairs_from_text(val))
                elif isinstance(payload, list):
                    for it in payload:
                        walk(it)
                elif isinstance(payload, str):
                    pairs.extend(_extract_pairs_from_text(payload))

            walk(data)

            # Ordenar pares por relevância em relação ao 'query' e limitar na sumarização
            def _strip_accents(s: str) -> str:
                try:
                    import unicodedata
                    return ''.join(c for c in unicodedata.normalize('NFD', s) if unicodedata.category(c) != 'Mn')
                except Exception:
                    return s

            def _score(q: str, nome: str | None) -> float:
                if not nome:
                    return 0.0
                import re as _re
                qn = _strip_accents((q or '').lower())
                nn = _strip_accents((nome or '').lower())
                score = 0.0
                for tok in _re.findall(r"[\wáéíóúâêîôûãõç]+", qn):
                    if tok and tok in nn:
                        score += 1.0
                for m in _re.findall(r"(\d+\s*(g|kg|ml|l|litro|un))", qn):
                    if m[0] in nn:
                        score += 1.5
                return score

            # Pontuar por relevância e filtrar apenas itens que casam com a consulta
            scored = [(pn, _score(query, pn[1])) for pn in pairs]
            # Ordena por score desc
            ordered = [pn for pn, sc in sorted(scored, key=lambda x: x[1], reverse=True)]
            # Mantém apenas os com score >= 1.0 (pelo menos um token da consulta)
            top_relevant = [pn for pn, sc in sorted(scored, key=lambda x: x[1], reverse=True) if sc >= 1.0][:3]
            # Fallback: se não houver relevantes, use os primeiros pares retornados
            used_pairs = top_relevant if top_relevant else ordered[:3]
            summary = _format_summary(used_pairs)
            if summary:
                sanitized = summary.replace("\n", "; ")
                logger.info(f"smart-responder resumo extraído: {sanitized}")
                final_resp = f"{summary}\n\n{json.dumps(data, indent=2, ensure_ascii=False)}"
            else:
                final_resp = json.dumps(data, indent=2, ensure_ascii=False)

            logger.info(f"Tamanho da resposta ean_lookup: {len(final_resp)} caracteres")
            return final_resp
        except Exception:
            # Se não for JSON, tentar extrair com regex do texto bruto
            pairs = _extract_pairs_from_text(text)
            # Aplicar o mesmo filtro de relevância no texto bruto
            def _strip_accents(s: str) -> str:
                try:
                    import unicodedata
                    return ''.join(c for c in unicodedata.normalize('NFD', s) if unicodedata.category(c) != 'Mn')
                except Exception:
                    return s
            def _score(q: str, nome: str | None) -> float:
                if not nome:
                    return 0.0
                import re as _re
                qn = _strip_accents((q or '').lower())
                nn = _strip_accents((nome or '').lower())
                score = 0.0
                for tok in _re.findall(r"[\wáéíóúâêîôûãõç]+", qn):
                    if tok and tok in nn:
                        score += 1.0
                for m in _re.findall(r"(\d+\s*(g|kg|ml|l|litro|un))", qn):
                    if m[0] in nn:
                        score += 1.5
                return score
            scored = [(pn, _score(query, pn[1])) for pn in pairs]
            top_relevant = [pn for pn, sc in sorted(scored, key=lambda x: x[1], reverse=True) if sc >= 1.0][:3]
            used_pairs = top_relevant if top_relevant else [pn for pn, _ in scored][:3]
            summary = _format_summary(used_pairs)
            if summary:
                return f"{summary}\n\n{text}"
            return text

    except requests.exceptions.Timeout:
        msg = "Erro: Timeout ao consultar smart-responder. Tente novamente."
        logger.error(msg)
        return msg
    except requests.exceptions.HTTPError as e:
        msg = f"Erro HTTP no smart-responder: {getattr(e.response, 'status_code', '?')} - {getattr(e.response, 'text', '')}"
        logger.error(msg)
        return msg
    except requests.exceptions.RequestException as e:
        msg = f"Erro ao consultar smart-responder: {str(e)}"
        logger.error(msg)
        return msg


def estoque_preco(ean: str) -> str:
    """
    Consulta preço e disponibilidade pelo EAN.

    Monta a URL completa concatenando o EAN ao final de settings.estoque_ean_base_url.
    Exemplo: {base}/7891149103300

    Args:
        ean: Código EAN do produto (apenas dígitos).

    Returns:
        JSON string com informações do produto ou mensagem de erro amigável.
    """
    base = (settings.estoque_ean_base_url or "").strip().rstrip("/")
    if not base:
        msg = "Erro: ESTOQUE_EAN_BASE_URL não configurado no .env"
        logger.error(msg)
        return msg

    # manter apenas dígitos no EAN
    ean_digits = "".join(ch for ch in ean if ch.isdigit())
    if not ean_digits:
        msg = "Erro: EAN inválido. Informe apenas números."
        logger.error(msg)
        return msg

    url = f"{base}/{ean_digits}"
    logger.info(f"Consultando estoque_preco por EAN: {url}")

    headers = {
        "Accept": "application/json",
    }

    try:
        resp = requests.get(url, headers=headers, timeout=10)
        resp.raise_for_status()

        # resposta esperada: lista de objetos
        try:
            data = resp.json()
        except json.JSONDecodeError:
            txt = resp.text
            logger.warning("Resposta não é JSON válido; retornando texto bruto")
            return txt

        # Se vier um único objeto, normalizar para lista
        items = data if isinstance(data, list) else ([data] if isinstance(data, dict) else [])

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

        # Possíveis chaves de quantidade de estoque (remover da saída)
        STOCK_QTY_KEYS = {
            "estoque", "qtd", "qtde", "qtd_estoque", "quantidade", "quantidade_disponivel",
            "quantidadeDisponivel", "qtdDisponivel", "qtdEstoque", "estoqueAtual", "saldo",
            "qty", "quantity", "stock", "amount", "qtd_produto", "qtd_movimentacao"
        }

        # Possíveis indicadores de disponibilidade
        BOOL_AVAIL_KEYS = ("disponibilidade", "disponivel", "available", "in_stock", "em_estoque", "ativo")
        STATUS_KEYS = ("situacao", "situacaoEstoque", "status", "statusEstoque")

        def _parse_float(val) -> float | None:
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

        def _status_available(d: Dict[str, Any]) -> bool:
            for k in STATUS_KEYS:
                v = d.get(k)
                if isinstance(v, str):
                    s = v.strip().lower()
                    if any(x in s for x in ["dispon", "em estoque", "in stock", "ativo"]):
                        return True
            return False

        def _is_available(d: Dict[str, Any]) -> bool:
            # APENAS produtos com estoque real positivo (> 0)
            if _has_positive_qty(d):
                return True
            
            return False

        def _extract_qty(d: Dict[str, Any]) -> float | None:
            for k in STOCK_QTY_KEYS:
                if k in d:
                    try:
                        return float(str(d.get(k)).replace(',', '.'))
                    except Exception:
                        pass
            return None

        def _extract_price(d: Dict[str, Any]) -> float | None:
            for k in PRICE_KEYS:
                if k in d:
                    val = _parse_float(d.get(k))
                    if val is not None:
                        return val
            return None

        sanitized: list[Dict[str, Any]] = []
        for it in items:
            if not isinstance(it, dict):
                continue
            if not _is_available(it):
                continue  # manter apenas itens com estoque/disponibilidade

            clean = {k: v for k, v in it.items() if k not in STOCK_QTY_KEYS}

            # Normalizar disponibilidade
            if "disponibilidade" not in clean:
                clean["disponibilidade"] = True

            # Normalizar preço em campo unificado
            price = _extract_price(it)
            if price is not None:
                clean["preco"] = price

            qty = _extract_qty(it)
            if qty is not None:
                clean["quantidade"] = qty

            sanitized.append(clean)

        logger.info(f"EAN {ean_digits}: {len(sanitized)} item(s) disponíveis após filtragem")

        return json.dumps(sanitized, indent=2, ensure_ascii=False)

    except requests.exceptions.Timeout:
        msg = "Erro: Timeout ao consultar preço/estoque por EAN. Tente novamente."
        logger.error(msg)
        return msg
    except requests.exceptions.HTTPError as e:
        status = getattr(e.response, "status_code", "?")
        body = getattr(e.response, "text", "")
        msg = f"Erro HTTP ao consultar EAN: {status} - {body}"
        logger.error(msg)
        return msg
    except requests.exceptions.RequestException as e:
        msg = f"Erro ao consultar EAN: {str(e)}"
        logger.error(msg)
        return msg

    # [Cleanup] Removido bloco duplicado de ean_lookup antigo fora de função
