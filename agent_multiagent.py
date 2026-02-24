"""
Agente de IA Multi-Agente para Atendimento de Supermercado usando LangGraph
Arquitetura: Vendedor (Agente Único)

Versão 6.0 - Single Agent Architecture
"""

from typing import Dict, Any, TypedDict, Annotated, List, Literal
import re
import operator
from langchain_openai import ChatOpenAI
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_core.messages import BaseMessage, SystemMessage, HumanMessage, AIMessage
from langchain_core.tools import tool
from langgraph.graph import StateGraph, END, START
from langgraph.prebuilt import create_react_agent
from langgraph.checkpoint.memory import MemorySaver
from pathlib import Path
import json

from config.settings import settings
from config.logger import setup_logger
from tools.http_tools import estoque, pedidos, alterar, estoque_preco, consultar_encarte

from tools.time_tool import get_current_time, search_message_history
from tools.redis_tools import (
    mark_order_sent, 
    add_item_to_cart, 
    get_cart_items, 
    remove_item_from_cart, 
    clear_cart,
    set_comprovante,
    get_comprovante,
    clear_comprovante,
    get_saved_address,
    save_address,
    get_order_session,
    normalize_phone,
    acquire_agent_lock,
    release_agent_lock,
    clear_order_session,
    start_order_session,
    clear_suggestions
)
from memory.hybrid_memory import HybridChatMessageHistory

logger = setup_logger(__name__)

# ============================================
# Estado Compartilhado do Grafo
# ============================================

def add_messages(left: list, right: list) -> list:
    """Função para combinar listas de mensagens."""
    return left + right

class AgentState(TypedDict):
    """Estado compartilhado entre os agentes."""
    messages: Annotated[list, add_messages]
    phone: str
    final_response: str  # Resposta final para o cliente

# ============================================
# Definição das Ferramentas (Separadas por Agente)
# ============================================

# --- FERRAMENTAS DO VENDEDOR ---

@tool
def busca_produto_tool(telefone: str, query: str) -> str:
    """
    Busca produtos e preços. Tenta ser inteligente: se não achar de primeira,
    refaz a busca com termos mais genéricos automaticamente.

    Retorna um JSON list com os dados dos produtos:
    [{"nome": "...", "categoria": "...", "preco": 10.0, "estoque": 5}]

    Usa chamadas na API FastAPI local.
    """
    from tools.db_search import search_products_db
    import json
    
    # 1. Busca Original
    resultado_json = search_products_db(query, telefone=telefone)
    resultados = json.loads(resultado_json)
    
    # Analisar qualidade dos resultados
    melhor_score = 0.0
    if resultados:
        melhor_score = max([r.get("match_score", 0.0) for r in resultados])
    
    # 2. Se score baixo (< 0.6) ou vazio, tenta limpar a query (Retry Automático)
    if not resultados or melhor_score < 0.6:
        # Tenta remover termos comuns que atrapalham (marcas, medidas, etc) se a query for longa
        termos_limpeza = ["de", "da", "do", "com", "sem", "g", "kg", "ml", "litros", "unidade"]
        query_limpa = " ".join([p for p in query.split() if p.lower() not in termos_limpeza])
        
        if query_limpa != query and len(query_limpa) > 3:
            logger.info(f"🔄 Retry Busca Inteligente: '{query}' -> '{query_limpa}'")
            resultado_retry_json = search_products_db(query_limpa, telefone=telefone)
            resultados_retry = json.loads(resultado_retry_json)
            
            # Se o retry trouxe algo melhor, usa ele
            score_retry = 0.0
            if resultados_retry:
                score_retry = max([r.get("match_score", 0.0) for r in resultados_retry])
            
            if score_retry > melhor_score:
                resultado_json = resultado_retry_json
                resultados = resultados_retry

    # 3. Análise de Ambiguidade de Categoria
    if resultados:
        # Filtrar apenas os que têm match razoável
        top_results = [r for r in resultados if r.get("match_score", 0) > 0.5]
        categorias = set()
        for r in top_results:
            cat = r.get("categoria", "").upper()
            # Simplificar categorias para evitar falsos positivos (ex: MERCEARIA DOCE vs MERCEARIA SALGADA)
            if "LIMPEZA" in cat: cat = "LIMPEZA"
            elif "HIGIENE" in cat: cat = "HIGIENE"
            elif "BEBIDAS" in cat: cat = "BEBIDAS"
            elif "AÇOUGUE" in cat or "CARNE" in cat: cat = "AÇOUGUE"
            elif "HORTIFRUTI" in cat or "LEGUMES" in cat: cat = "HORTIFRUTI"
            
            if cat:
                categorias.add(cat)
        
        # Se encontrou categorias muito distintas (ex: LIMPEZA e HIGIENE)
        if len(categorias) > 1 and "LIMPEZA" in categorias and "HIGIENE" in categorias:
             warning = {
                 "id": "AVISO_AMBIGUIDADE",
                 "nome": "⚠️ AMBIGUIDADE DETECTADA",
                 "preco": 0.0,
                 "estoque": 0,
                 "match_ok": False,
                 "aviso": f"Encontrei produtos de categorias diferentes ({', '.join(categorias)}). PERGUNTE ao cliente qual ele deseja antes de adicionar."
             }
             resultados.insert(0, warning)
             resultado_json = json.dumps(resultados, ensure_ascii=False)

    return resultado_json

@tool
def add_item_tool(telefone: str, produto: str, quantidade: float = 1.0, observacao: str = "", preco: float = 0.0, unidades: int = 0) -> str:
    """
    Adicionar um item ao pedido do cliente.
    USAR IMEDIATAMENTE quando o cliente demonstrar intenção de compra.
    
    Para produtos vendidos por KG (frutas, legumes, carnes):
    - quantidade: peso em kg (ex: 0.45 para 450g)
    - unidades: número de unidades pedidas (ex: 3 para 3 tomates)
    - preco: preço por kg
    
    Para produtos unitários:
    - quantidade: número de itens
    - unidades: deixar 0
    - preco: preço por unidade
    """
    
    # IMPORTAR AQUI para evitar ciclo de importação
    from tools.redis_tools import get_suggestions
    import difflib

    prod_lower = produto.lower().strip()
    
    # 0. TENTATIVA DE RECUPERAÇÃO DE PREÇO (Auto-Healing)
    # Se o agente esqueceu o preço (0.0), tentamos achar nas sugestões recentes
    if preco <= 0.01:
        sugestoes = get_suggestions(telefone)
        if sugestoes:
            # Tentar match exato ou fuzzy
            melhor_match = None
            maior_score = 0
            
            for sug in sugestoes:
                nome_sug = sug.get("nome", "").lower()
                # Match exato de substring
                if prod_lower in nome_sug or nome_sug in prod_lower:
                    melhor_match = sug
                    break
                
                # Match fuzzy (difflib)
                ratio = difflib.SequenceMatcher(None, prod_lower, nome_sug).ratio()
                if ratio > 0.6 and ratio > maior_score:
                    maior_score = ratio
                    melhor_match = sug
            
            if melhor_match:
                preco_recuperado = float(melhor_match.get("preco", 0.0))
                if preco_recuperado > 0:
                    preco = preco_recuperado
                    logger.info(f"✨ [AUTO-HEAL] Preço recuperado para '{produto}': R$ {preco:.2f} (baseado em '{melhor_match.get('nome')}')")
    
    # BLOQUEIO: Não permitir adicionar item sem preço válido
    if preco <= 0.01:
        logger.warning(f"🚫 [ADD_ITEM] Bloqueado: '{produto}' sem preço válido (R$ {preco:.2f}). Use busca_produto_tool primeiro.")
        return f"❌ Não consegui encontrar o preço de '{produto}'. Use busca_produto_tool para verificar o preço antes de adicionar."
    
    # Validar match_ok nas sugestões — se o produto não passou na validação, avisar
    sugestoes_validacao = get_suggestions(telefone)
    if sugestoes_validacao:
        for sug in sugestoes_validacao:
            nome_sug = sug.get("nome", "").lower()
            if prod_lower in nome_sug or nome_sug in prod_lower:
                if not sug.get("match_ok", True):
                    logger.warning(f"⚠️ [ADD_ITEM] Produto '{produto}' tem match_ok=false. Pedindo confirmação.")
                    return f"⚠️ '{produto}' não parece ser uma correspondência exata. Confirme com o cliente qual opção ele deseja antes de adicionar."
                break
    
    if unidades > 0 and quantidade <= 0.01:
         logger.warning(f"⚠️ [ADD_ITEM] Item '{produto}' com unidades={unidades} mas peso zerado. O LLM deveria ter calculado.")
    
    # Construir JSON do item para add_item_to_cart
    import json
    item_data = {
        "produto": produto,
        "quantidade": quantidade,
        "observacao": observacao,
        "preco": preco,
        "unidades": unidades
    }
    item_json = json.dumps(item_data, ensure_ascii=False)
    
    success = add_item_to_cart(telefone, item_json)
    if success:
         # Calcular valor estimado TOTAL (já que o peso deve vir correto do LLM)
         valor_estimado = quantidade * preco
         if unidades > 0:
             return f"✅ Adicionado: {unidades}x {produto} ({quantidade:.3f}kg) - Total Estimado: R$ {valor_estimado:.2f}"
         else:
             return f"✅ Adicionado: {quantidade} {produto} - Total: R$ {valor_estimado:.2f}"
    return "❌ Erro ao adicionar item."

@tool
def reset_pedido_tool(telefone: str) -> str:
    """
    Zera o pedido do cliente (carrinho, sessão, comprovante e sugestões) e inicia uma nova sessão.
    """
    telefone = normalize_phone(telefone)
    clear_cart(telefone)
    clear_order_session(telefone)
    clear_comprovante(telefone)
    clear_suggestions(telefone)
    start_order_session(telefone)
    return "✅ Pedido zerado com sucesso! Pode me enviar a nova lista de itens."

@tool
def ver_pedido_tool(telefone: str) -> str:
    """Ver os itens atuais no pedido do cliente."""
    items = get_cart_items(telefone)
    if not items:
        return "📝 Sua lista está vazia."
    
    lines = ["📝 **Resumo do Pedido:**"]
    total = 0.0
    for i, item in enumerate(items, 1):
        nome = item.get("produto", "Item")
        qtd = item.get("quantidade", 1)
        preco = item.get("preco", 0)
        unidades = item.get("unidades", 0)
        
        valor = preco * qtd
        total += valor
        
        if unidades > 0:
            lines.append(f"{i}. {unidades}x {nome} - Total Estimado: R$ {valor:.2f}")
        else:
            qtd_display = int(qtd) if qtd == int(qtd) else qtd
            lines.append(f"{i}. {qtd_display}x {nome} - R$ {valor:.2f}")
    
    lines.append(f"\n**Subtotal:** R$ {total:.2f}")
    return "\n".join(lines)

@tool
def remove_item_tool(telefone: str, item_index: int, quantidade: float = 0) -> str:
    """
    Remover um item do carrinho pelo número (índice 1-based).
    
    Se quantidade = 0 ou não informada: Remove o item INTEIRO.
    Se quantidade > 0: Remove APENAS essa quantidade (ex: tirar 1 unidade de 3).
    
    Exemplos:
    - Cliente: "tira o item 2" → remove_item_tool(tel, 2, 0) → Remove item 2 inteiro
    - Cliente: "tira 1 nescau" → remove_item_tool(tel, 2, 1) → Remove 1 unidade do item 2
    """
    from tools.redis_tools import remove_item_from_cart, update_item_quantity
    
    # Converter para índice 0-based
    idx_zero_based = int(item_index) - 1
    
    if quantidade > 0:
        # Remoção PARCIAL - reduz quantidade
        result = update_item_quantity(telefone, idx_zero_based, quantidade)
        
        if result["success"]:
            if result["removed_completely"]:
                return f"✅ {result['item_name']} removido completamente do pedido."
            else:
                return f"✅ Removido {quantidade} de {result['item_name']}. Agora tem {result['new_quantity']} no pedido."
        return f"❌ Erro: Item {item_index} não encontrado."
    else:
        # Remoção COMPLETA - comportamento original
        success = remove_item_from_cart(telefone, idx_zero_based)
        if success:
            return f"✅ Item {item_index} removido do pedido."
        return f"❌ Erro: Item {item_index} não encontrado."


@tool("ean")
def ean_tool_alias(query: str) -> str:
    """Buscar EAN/infos do produto na base de conhecimento."""
    q = (query or "").strip()
    if q.startswith("{") and q.endswith("}"): q = ""
    return ean_lookup(q)

@tool("estoque")
def estoque_preco_alias(ean: str) -> str:
    """Consulta preço e disponibilidade pelo EAN (apenas dígitos)."""
    return estoque_preco(ean)


# ============================================
# Ferramentas do Analista (Sub-Agente)
# ============================================



# --- FERRAMENTAS DO CAIXA ---

@tool
def calcular_total_tool(telefone: str, taxa_entrega: float = 0.0) -> str:
    """
    Calcula o valor exato do pedido somando itens do carrinho + taxa de entrega.
    Use SEMPRE antes de informar o total final ao cliente.
    
    Args:
    - telefone: Telefone do cliente
    - taxa_entrega: Valor da taxa de entrega a ser somada (se houver)
    """
    items = get_cart_items(telefone)
    if not items:
        return "❌ Pedido vazio. Não é possível calcular total."
    
    subtotal = 0.0
    item_details = []
    
    for i, item in enumerate(items):
        preco = float(item.get("preco", 0.0))
        qtd = float(item.get("quantidade", 1.0))
        nome = item.get("produto", "Item")
        
        valor_item = round(preco * qtd, 2)
        subtotal += valor_item
        item_details.append(f"- {nome}: R$ {valor_item:.2f}")
        
    subtotal = round(subtotal, 2)
    taxa_entrega = round(float(taxa_entrega), 2)
    total_final = round(subtotal + taxa_entrega, 2)
    
    res = (
        f"📝 **Cálculo Oficial do Sistema:**\n"
        f"Subtotal: R$ {subtotal:.2f}\n"
        f"Taxa de Entrega: R$ {taxa_entrega:.2f}\n"
        f"----------------\n"
        f"💰 **TOTAL FINAL: R$ {total_final:.2f}**"
    )
    return res

@tool
def salvar_endereco_tool(telefone: str, endereco: str) -> str:
    """
    Salva o endereço do cliente para usar depois no fechamento do pedido.
    Use IMEDIATAMENTE quando o cliente informar o endereço (mesmo no início da conversa).
    """
    if save_address(telefone, endereco):
        return f"✅ Endereço salvo: {endereco}"
    return "❌ Erro ao salvar endereço."

@tool
def finalizar_pedido_tool(cliente: str, telefone: str, endereco: str, forma_pagamento: str, itens_json: str, observacao: str = "", comprovante: str = "", taxa_entrega: float = 0.0) -> str:
    """
    Finalizar o pedido enviando TODOS os itens confirmados.
    Use quando o cliente confirmar que quer fechar a compra e repasse todos os itens do contexto da conversa.
    
    Args:
    - cliente: Nome do cliente
    - telefone: Telefone (com DDD)
    - endereco: Endereço de entrega completo
    - forma_pagamento: Pix, Cartão ou Dinheiro
    - itens_json: String em formato JSON com todos os itens, ex: [{"produto": "Arroz", "quantidade": 2.0, "preco": 20.0}]
    - observacao: Observações extras (troco, etc)
    - comprovante: URL do comprovante PIX (se houver)
    - taxa_entrega: Valor da taxa de entrega em reais (opcional, padrão 0)
    """
    import json as json_lib
    
    try:
        items = json_lib.loads(itens_json)
    except Exception as e:
        return f"❌ Erro ao ler os itens do pedido: erro de formato JSON - {e}. Corrija o JSON e tente novamente."
        
    if not items:
        return "❌ O pedido está vazio! Você deve repassar a lista de produtos confirmados."
    
    comprovante_salvo = get_comprovante(telefone)
    comprovante_final = comprovante or comprovante_salvo or ""
    
    total = 0.0
    itens_formatados = []
    
    for item in items:
        preco = float(item.get("preco", 0.0))
        quantidade = float(item.get("quantidade", 1.0))
        unidades = int(item.get("unidades", 0))
        obs_item = item.get("observacao", "")
        total += preco * quantidade
        
        nome_produto = item.get("produto", item.get("nome_produto", "Produto"))
        
        if unidades > 0:
            qtd_api = unidades
            valor_estimado = round(preco * quantidade, 2)
            preco_unitario_api = round(valor_estimado / unidades, 2)
            obs_peso = f"Peso estimado: {quantidade:.3f}kg (~R${valor_estimado:.2f}). PESAR para confirmar valor."
            if obs_item:
                obs_item = f"{obs_item}. {obs_peso}"
            else:
                obs_item = obs_peso
        else:
            if quantidade < 1 or quantidade != int(quantidade):
                qtd_api = 1
            else:
                qtd_api = int(quantidade)
            preco_unitario_api = round(preco, 2)
        
        itens_formatados.append({
            "nome_produto": nome_produto,
            "quantidade": qtd_api,
            "preco_unitario": preco_unitario_api,
            "observacao": obs_item
        })
    
    payload = {
        "nome_cliente": cliente,
        "telefone": telefone,
        "endereco": endereco or "A combinar",
        "forma": forma_pagamento,
        "observacao": observacao or "",
        "comprovante_pix": comprovante_final or None,
        "taxa_entrega": round(taxa_entrega, 2) if taxa_entrega > 0 else None,
        "itens": itens_formatados
    }
    
    # Soma taxa ao total para exibição
    if taxa_entrega > 0:
        total += taxa_entrega
    
    json_body = json_lib.dumps(payload, ensure_ascii=False)
    
    # AUDIT LOG: Registrar payload completo antes de enviar
    try:
        from datetime import datetime
        audit_entry = {
            "timestamp": datetime.now().isoformat(),
            "telefone": telefone,
            "cliente": cliente,
            "total": round(total, 2),
            "itens_count": len(itens_formatados),
            "payload": payload
        }
        import os
        os.makedirs("logs", exist_ok=True)
        with open("logs/pedidos_audit.jsonl", "a", encoding="utf-8") as f:
            f.write(json_lib.dumps(audit_entry, ensure_ascii=False) + "\n")
        logger.info(f"📋 [AUDIT] Pedido registrado para {telefone} - R$ {total:.2f}")
    except Exception as audit_err:
        logger.warning(f"⚠️ Falha no audit log: {audit_err}")
    
    result = pedidos(json_body)
    
    if "sucesso" in result.lower() or "✅" in result:
        # NÃO LIMPAR O CARRINHO AQUI!
        # O carrinho deve persistir por 15 minutos (TTL do Redis) para permitir alterações.
        
        mark_order_sent(telefone, result) # Atualiza o status da sessão para 'sent'
        
        return f"{result}\n\n💰 **Valor Total Processado:** R$ {total:.2f}\n(O agente DEVE usar este valor na resposta)"
        
    return result

# --- FERRAMENTAS COMPARTILHADAS ---

@tool
def time_tool() -> str:
    """Retorna a data e hora atual."""
    return get_current_time()

@tool
def search_history_tool(telefone: str, keyword: str = None) -> str:
    """Busca mensagens anteriores do cliente com horários."""
    return search_message_history(telefone, keyword)

# ============================================
# Listas de Ferramentas por Agente
# ============================================

VENDEDOR_TOOLS = [
    busca_produto_tool,
    time_tool,
    salvar_endereco_tool,
    finalizar_pedido_tool,
]

# ============================================
# Funções de Carregamento de Prompts
# ============================================

def load_prompt(filename: str) -> str:
    """Carrega um prompt do diretório prompts/"""
    base_dir = Path(__file__).resolve().parent
    prompt_path = base_dir / "prompts" / filename
    
    logger.info(f"📄 Carregando prompt: {prompt_path}")
    
    try:
        text = prompt_path.read_text(encoding="utf-8")
        text = text.replace("{base_url}", settings.supermercado_base_url)
        text = text.replace("{ean_base}", settings.estoque_ean_base_url)
        return text
    except Exception as e:
        logger.error(f"Falha ao carregar prompt {filename}: {e}")
        raise

# ============================================
# Construção dos LLMs
# ============================================

def _build_llm(temperature: float = 0.0, model_override: str = None):
    """Constrói um LLM baseado nas configurações."""
    model = model_override or getattr(settings, "llm_model", "gemini-1.5-flash")
    provider = getattr(settings, "llm_provider", "google")
    
    if provider == "google":
        logger.debug(f"🚀 Usando Google Gemini: {model}")
        return ChatGoogleGenerativeAI(
            model=model,
            api_key=settings.google_api_key,
            temperature=temperature,
            timeout=120,  # Timeout de 2 minutos para evitar hang
            max_retries=2,
        )
    else:
        logger.debug(f"🚀 Usando OpenAI (compatível): {model}")
        
        client_kwargs = {}
        if settings.openai_api_base:
            client_kwargs["base_url"] = settings.openai_api_base

        return ChatOpenAI(
            model=model,
            api_key=settings.openai_api_key,
            temperature=temperature,
            **client_kwargs
        )

def _build_fast_llm():
    """Constrói um LLM rápido e leve para o Orquestrador."""
    # Usa o mesmo modelo mas com temperatura 0 para determinismo
    return _build_llm(temperature=0.0)

# ============================================
# Nós do Grafo (Agentes)
# ============================================


# ============================================
# Helpers
# ============================================

def _extract_response(result: Any) -> str:
    """Extrai a resposta de texto de um resultado do LangGraph/LangChain."""
    def _content_to_str(content):
        """Converte content (str ou list) para string."""
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            # Novo formato LangChain v1.x: content é lista de blocos
            parts = []
            for block in content:
                if isinstance(block, str):
                    parts.append(block)
                elif isinstance(block, dict) and "text" in block:
                    parts.append(block["text"])
            return "\n".join(parts)
        return str(content) if content else ""

    if isinstance(result, dict) and "messages" in result:
        msgs = result["messages"]
        if msgs:
            last_msg = msgs[-1]
            if isinstance(last_msg, BaseMessage):
                return _content_to_str(last_msg.content)
            return str(last_msg)
    elif isinstance(result, BaseMessage):
        return _content_to_str(result.content)
    return str(result)

# Orquestrador removido



def vendedor_node(state: AgentState) -> dict:
    """
    Nó Vendedor: Agente especializado em vendas com prompt completo.
    """
    logger.info("👩‍💼 [VENDEDOR] Processando...")
    
    # set_current_phone(state["phone"]) # REMOVIDO: Contexto do analista
    
    prompt = load_prompt("vendedor.md")
    llm = _build_llm(temperature=0.0)  # Temperatura 0 para seguir regras do prompt
    
    # Criar agente ReAct com as ferramentas do vendedor
    agent = create_react_agent(llm, VENDEDOR_TOOLS, prompt=prompt)
    
    logger.info(f"👩‍💼 [VENDEDOR] Agente criado. Invocando...")
    
    # Configuração
    config = {
        "configurable": {"thread_id": state["phone"]},
        "recursion_limit": 25
    }
    
    def _check_hallucination(agent_result: dict, agent_response: str) -> tuple[bool, str, set]:
        messages_local = agent_result.get("messages", []) if isinstance(agent_result, dict) else []
        tools_called_local = set()
        for msg in messages_local:
            if isinstance(msg, AIMessage) and hasattr(msg, "tool_calls") and msg.tool_calls:
                for call in msg.tool_calls:
                    tools_called_local.add(call["name"])

        response_lower_local = (agent_response if isinstance(agent_response, str) else str(agent_response or "")).lower()
        hallucination_detected_local = False
        hallucination_reason_local = ""

        # 1. REMOVIDO: A regra de 'add_item_tool' não faz mais sentido no modo Sem Carrinho.

        # 2. Disse "encontrei" sem chamar busca_produto_tool
        if "encontrei" in response_lower_local:
            if "busca_produto_tool" not in tools_called_local:
                hallucination_detected_local = True
                hallucination_reason_local = "disse 'encontrei' sem chamar busca_produto_tool"

        # 3. Mencionou preço (R$) sem buscar no banco primeiro
        if not hallucination_detected_local:
            import re as _re
            # Regex para capturar R$ XX,XX ou "50 reais"
            price_mentions = _re.findall(r"(?:r\$\s*|(?:\d+)\s+reais)\d+(?:[,\.]\d{2})?", response_lower_local)
            
            # Se mencionou preço, DEVEMOS ter contexto de busca ou de fechamento
            if price_mentions:
                # Contextos permitidos para falar de preço SEM buscar produto:
                # 1. Está fechando o pedido (calcular total)
                is_checkout = "calcular_total_tool" in tools_called_local or "finalizar_pedido_tool" in tools_called_local
                # 2. Usuário falou de pagamento/troco (contexto da mensagem atual)
            # PEGAR A ÚLTIMA MENSAGEM DO TIPO HUMANMESSAGE PARA GARANTIR
            user_msgs = [m for m in state["messages"] if isinstance(m, HumanMessage)]
            user_msg_lower = (user_msgs[-1].content or "").lower() if user_msgs else ""
            
            logger.info(f"🔍 [HALLUCINATION_CHECK] User Msg: '{user_msg_lower}'")

            payment_terms = ["troco", "pagamento", "pix", "cartao", "cartão", "dinheiro", "nota", "card", "cash"]
            is_payment_context = any(t in user_msg_lower for t in payment_terms)
            
            # Se for contexto de pagamento, LOGAR que dispensou checagem
            if is_payment_context:
                logger.debug("💰 Contexto de pagamento detectado. Dispensando verificação de preço.")
                
                # Se NÃO for checkout E NÃO for contexto de pagamento, aí sim exige busca
                if not is_checkout and not is_payment_context:
                    if "busca_produto_tool" not in tools_called_local and "add_item_tool" not in tools_called_local:
                        hallucination_detected_local = True
                        hallucination_reason_local = f"citou preços ({price_mentions[:3]}) sem consultar busca_produto_tool (e não é fechamento)"

        return hallucination_detected_local, hallucination_reason_local, tools_called_local

    tools_called_with_messages = []
    
    result = agent.invoke({"messages": state["messages"]}, config)
    response = _extract_response(result)

    hallucination_detected, hallucination_reason, tools_called = _check_hallucination(result, response)
    if hallucination_detected:
        logger.warning(f"⚠️ ALUCINAÇÃO DETECTADA: {hallucination_reason}. Tools usadas: {tools_called}")

        # Montar retry com o motivo ESPECÍFICO do erro
        retry_instruction = SystemMessage(
            content=(
                f"RETRY INTERNO (não mostrar ao cliente): sua última resposta foi REJEITADA.\n"
                f"MOTIVO: {hallucination_reason}\n"
                f"Tools que você chamou: {', '.join(tools_called) if tools_called else 'NENHUMA'}\n\n"
                f"CORRIJA seguindo estas etapas OBRIGATÓRIAS:\n"
                f"1. Se o cliente pediu produtos, chame busca_produto_tool para CADA produto.\n"
                f"2. Analise os resultados da busca (preço, estoque, match_ok).\n"
                f"3. Chame add_item_tool para CADA produto encontrado, usando o preço RETORNADO pela busca.\n"
                f"4. SÓ DEPOIS de chamar add_item_tool, responda ao cliente confirmando.\n"
                f"5. NUNCA diga 'adicionei' se não chamou add_item_tool.\n"
                f"6. NUNCA cite preços se não chamou busca_produto_tool.\n\n"
                f"Processe o pedido do cliente AGORA chamando as ferramentas corretas."
            )
        )
        retry_messages = list(state["messages"]) + [retry_instruction]
        retry_result = agent.invoke({"messages": retry_messages}, config)
        retry_response = _extract_response(retry_result)
        retry_hallucination, retry_reason, retry_tools = _check_hallucination(retry_result, retry_response)
        if not retry_hallucination and retry_response:
            result = retry_result
            response = retry_response
        else:
            logger.warning(f"⚠️ ALUCINAÇÃO (RETRY) DETECTADA: {retry_reason}. Tools: {retry_tools}")
            response = "Desculpe, tive um problema técnico. Pode me dizer novamente o que você gostaria?"

    logger.info(f"👩‍💼 [VENDEDOR] Resposta: {response[:100]}...")
    
    return {
        "final_response": response,
        "messages": result.get("messages", [])[-1:] if result.get("messages") else []
    }


# Caixa removido e Roteamento removido


# ============================================
# Construção do Grafo
# ============================================

def build_multi_agent_graph():
    """Constrói o StateGraph com a arquitetura de Agente Único (Vendedor)."""
    
    graph = StateGraph(AgentState)
    
    # Adicionar nó único
    graph.add_node("vendedor", vendedor_node)
    
    # Fluxo: START → Vendedor → END
    graph.add_edge(START, "vendedor")
    graph.add_edge("vendedor", END)
    
    return graph.compile()

# ============================================
# Função Principal
# ============================================

def run_agent_langgraph(telefone: str, mensagem: str) -> Dict[str, Any]:
    """
    Executa o agente multi-agente. Suporta texto e imagem (via tag [MEDIA_URL: ...]).
    """
    telefone = normalize_phone(telefone)
    logger.info(f"[MULTI-AGENT] Telefone: {telefone} | Msg: {mensagem[:50]}...")
    lock_token = acquire_agent_lock(telefone)
    if not lock_token:
        return {
            "output": "Estou finalizando sua última solicitação. Me manda só um instante e eu já te respondo.",
            "error": "busy"
        }
    
    # 1. Extrair URL de imagem se houver
    image_url = None
    clean_message = mensagem
    
    media_match = re.search(r"\[MEDIA_URL:\s*(.*?)\]", mensagem)
    if media_match:
        image_url = media_match.group(1)
        clean_message = mensagem.replace(media_match.group(0), "").strip()
        if not clean_message:
            clean_message = "Analise esta imagem/comprovante enviada."
        logger.info(f"📸 Mídia detectada: {image_url}")

    # 1. Recuperar histórico (Híbrido: Redis=Contexto, Postgres=Log)
    from memory.hybrid_memory import HybridChatMessageHistory
    history_handler = HybridChatMessageHistory(session_id=telefone, redis_ttl=getattr(settings, 'redis_ttl', 2400))
    
    previous_messages = []
    try:
        previous_messages = history_handler.messages
    except Exception as e:
        logger.error(f"Erro ao buscar histórico híbrido: {e}")

    # 2. Persistir mensagem do usuário (Salva em Redis e Postgres)
    try:
        history_handler.add_user_message(mensagem)
    except Exception as e:
        logger.error(f"Erro ao salvar msg user no histórico: {e}")

    try:
        # CONSTRUIR O GRAFO A CADA EXECUÇÃO para garantir ISOLAMENTO TOTAL
        # Evita bugs de vazamento de contexto (MemorySaver global)
        graph = build_multi_agent_graph()
        
        # 3. Construir mensagem com contexto
        from tools.time_tool import get_current_time
        hora_atual = get_current_time()
        contexto = f"[TELEFONE_CLIENTE: {telefone}]\n[HORÁRIO_ATUAL: {hora_atual}]\n"
        
        if image_url:
            contexto += f"[URL_IMAGEM: {image_url}]\n"
        
        # 3.1 Consultar dados cadastrados do cliente (Sempre injetar para endereço/bairro)
        try:
            from tools.http_tools import consultar_cliente
            cliente_data = consultar_cliente(telefone)
            if cliente_data and cliente_data.get("nome"):
                nome_cli = cliente_data["nome"]
                endereco_cli = cliente_data.get("endereco", "")
                bairro_cli = cliente_data.get("bairro", "")
                cidade_cli = cliente_data.get("cidade", "")
                total_ped = cliente_data.get("total_pedidos", 0)
                endereco_full = ", ".join(p for p in [endereco_cli, bairro_cli, cidade_cli] if p.strip())
                
                # Se for a primeira mensagem, injeta a tag de Saudação. Se não, apenas informa os dados silenciando o trigger.
                if len(previous_messages) == 0:
                    contexto += f"[CLIENTE_CADASTRADO: {nome_cli} | Endereço: {endereco_full} | Pedidos anteriores: {total_ped}]\n[SESSÃO] Nova conversa.\n"
                else:
                    contexto += f"[DADOS DO CLIENTE PARA ENTREGA: {nome_cli} | Endereço: {endereco_full}]\n"
                
                logger.info(f"👤 Cliente cadastrado: {nome_cli} ({total_ped} pedidos)")
            else:
                if len(previous_messages) == 0:
                     contexto += "[CLIENTE_NOVO: não cadastrado]\n[SESSÃO] Nova conversa.\n"
        except Exception as e:
            logger.warning(f"⚠️ Falha ao consultar cliente: {e}")
            if len(previous_messages) == 0:
                 contexto += "[CLIENTE_NOVO: não cadastrado]\n[SESSÃO] Nova conversa.\n"
        
        # Expansão de mensagens curtas
        mensagem_expandida = clean_message
        msg_lower = clean_message.lower().strip()
        
        if msg_lower in ["sim", "s", "ok", "pode", "isso", "quero", "beleza", "blz", "bora", "vamos"]:
            ultima_pergunta_ia = ""
            for msg in reversed(previous_messages):
                if isinstance(msg, AIMessage) and msg.content:
                    content = msg.content if isinstance(msg.content, str) else str(msg.content)
                    if content.strip() and not content.startswith("["):
                        ultima_pergunta_ia = content[:200]
                        break
            
            if ultima_pergunta_ia:
                mensagem_expandida = f"O cliente respondeu '{clean_message}' CONFIRMANDO. Sua mensagem anterior foi: \"{ultima_pergunta_ia}...\". Se você sugeriu produtos, use busca_produto_tool para confirmar preço e só então adicione os itens confirmados com add_item_tool. Não invente preço."
                logger.info(f"🔄 Mensagem curta expandida: '{clean_message}'")
        elif msg_lower in ["nao", "não", "n", "nope", "nao quero", "não quero"]:
            mensagem_expandida = f"O cliente respondeu '{clean_message}' (NEGATIVO). Pergunte se precisa de mais alguma coisa."
        
        contexto += "\n"
        
        # Construir mensagem (multimodal se tiver imagem)
        if image_url:
            message_content = [
                {"type": "text", "text": contexto + mensagem_expandida},
                {"type": "image_url", "image_url": {"url": image_url}}
            ]
            current_message = HumanMessage(content=message_content)
        else:
            current_message = HumanMessage(content=contexto + mensagem_expandida)

        # 4. Montar estado inicial
        all_messages = list(previous_messages) + [current_message]
        
        initial_state = {
            "messages": all_messages,
            "phone": telefone,
            "final_response": ""
        }
        
        logger.info(f"📨 Enviando {len(all_messages)} mensagens para o grafo")
        
        config = {"configurable": {"thread_id": telefone}}
        
        # 5. Executar o grafo
        result = graph.invoke(initial_state, config)
        
        # 6. Extrair resposta final
        output = result.get("final_response", "")
        
        if not output or not output.strip():
            logger.warning("⚠️ Resposta vazia, tentando extrair das mensagens")
            output = _extract_response({"messages": result.get("messages", [])})
        
        if not output or not output.strip():
            output = "Desculpe, tive um problema ao processar. Pode repetir por favor?"
        
        logger.info(f"✅ [MULTI-AGENT] Resposta: {output[:200]}...")
        
        # 7. Salvar histórico (IA)
        if history_handler:
            try:
                history_handler.add_ai_message(output)
            except Exception as e:
                logger.error(f"Erro DB AI: {e}")

        return {"output": output, "error": None}
        
    except Exception as e:
        logger.error(f"Falha agente: {e}", exc_info=True)
        return {"output": "Tive um problema técnico, tente novamente.", "error": str(e)}
    finally:
        try:
            release_agent_lock(telefone, lock_token)
        except Exception:
            pass


def get_session_history(session_id: str) -> HybridChatMessageHistory:
    return HybridChatMessageHistory(session_id=normalize_phone(session_id), redis_ttl=settings.human_takeover_ttl or 900)

# Alias para compatibilidade
run_agent = run_agent_langgraph
