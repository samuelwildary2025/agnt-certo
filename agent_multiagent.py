"""
Agente de IA Multi-Agente para Atendimento de Supermercado usando LangGraph
Arquitetura: Orquestrador + Vendedor + Caixa

Versão 5.0 - Multi-Agent Architecture
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
from tools.http_tools import estoque, pedidos, alterar, ean_lookup, estoque_preco, consultar_encarte
from tools.search_agent import analista_produtos_tool
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
    current_agent: str  # "orchestrator" | "vendedor" | "caixa"
    intent: str  # "vendas" | "checkout" | "unknown"
    phone: str
    final_response: str  # Resposta final para o cliente

# ============================================
# Definição das Ferramentas (Separadas por Agente)
# ============================================

# --- FERRAMENTAS DO VENDEDOR ---

@tool
def estoque_tool(url: str) -> str:
    """
    Consultar estoque e preço atual dos produtos no sistema do supermercado.
    Ex: 'https://.../api/produtos/consulta?nome=arroz'
    """
    return estoque(url)

@tool
def add_item_tool(telefone: str, produto: str, quantidade: float = 1.0, observacao: str = "", preco: float = 0.0, unidades: int = 0) -> str:
    """
    Adicionar um item ao carrinho de compras do cliente.
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
    
    # --- LOGICA ANTIGA REMOVIDA: A conversão agora é feita pelo LLM ---
    # O LLM deve calcular e enviar 'quantidade' já com o peso em KG.
    # Ex: 6 pães * 0.050kg = 0.300kg -> Prompt envia quantidade=0.3
    
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
def view_cart_tool(telefone: str) -> str:
    """Ver os itens atuais no carrinho do cliente."""
    items = get_cart_items(telefone)
    if not items:
        return "🛒 Carrinho vazio."
    
    lines = ["🛒 **Carrinho atual:**"]
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
def remove_item_tool(telefone: str, item_index: int) -> str:
    """
    Remover um item do carrinho pelo número (índice 1-based, como mostrado no view_cart).
    Ex: Para remover o item 1, passe 1.
    """
    # Converter para índice 0-based
    idx_zero_based = int(item_index) - 1
    success = remove_item_from_cart(telefone, idx_zero_based)
    if success:
        return f"✅ Item {item_index} removido do carrinho."
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

# Variável de contexto para compartilhar telefone entre Vendedor e Analista
from contextvars import ContextVar
_current_phone: ContextVar[str] = ContextVar('current_phone', default='')

def set_current_phone(phone: str):
    """Define o telefone atual para o contexto de execução."""
    _current_phone.set(phone)

def get_current_phone() -> str:
    """Obtém o telefone atual do contexto de execução."""
    return _current_phone.get()


# ============================================
# Ferramentas do Analista (Sub-Agente)
# ============================================

@tool("banco_vetorial")
def banco_vetorial_tool(query: str, limit: int = 10) -> str:
    """
    Busca produtos no banco de dados vetorial do supermercado.
    Retorna lista de produtos similares ao termo buscado com nome, preço e disponibilidade.
    
    Args:
        query: Termo de busca (ex: "coca 2l", "arroz tio joao", "picadinho")
        limit: Quantidade máxima de resultados (padrão 10)
    """
    from tools.search_agent import _build_options
    import json
    
    options = _build_options(query, limit=limit)
    
    if not options:
        return json.dumps({"produtos": [], "mensagem": f"Nenhum produto encontrado para '{query}'"}, ensure_ascii=False)
    
    # Formatar para o Analista entender facilmente
    produtos = []
    for opt in options:
        produtos.append({
            "nome": opt.get("nome", ""),
            "preco": opt.get("preco", 0),
            "disponivel": True,  # Se chegou aqui, tem estoque
            "categoria": opt.get("categoria", "")
        })
    
    return json.dumps({"produtos": produtos, "termo": query}, ensure_ascii=False)


def _call_analista(produtos: str) -> str:
    """
    [VENDEDOR -> ANALISTA]
    Analista de Produtos que busca e organiza produtos.
    
    Fluxo simplificado (sem LLM extra):
    1. Recebe pedido do Vendedor (ex: "coca 2l, arroz")
    2. Busca no banco vetorial + verifica estoque
    3. Organiza e retorna lista formatada com preços
    
    Args:
        produtos: Termos de busca separados por vírgula
    """
    if not produtos or not produtos.strip():
        return "❌ Informe os produtos para o analista."
    
    telefone = get_current_phone()
    
    # Usar busca direta com formatação melhorada (sem LLM extra)
    # O analista_produtos_tool já retorna JSON organizado com lista_formatada
    return analista_produtos_tool(produtos, telefone=telefone)


@tool("busca_analista")
def busca_analista_tool(produtos: str) -> str:
    """
    Encaminha nomes de produtos para o analista e retorna produto + preço.
    Use quando o cliente pedir itens e você precisar do preço oficial.
    """
    return _call_analista(produtos)

@tool
def consultar_encarte_tool() -> str:
    """
    Consulta o encarte (folheto de ofertas) atual do supermercado.
    Use APENAS se o cliente perguntar explicitamente sobre ofertas, promoções ou encarte.
    
    Returns:
        JSON com a URL (campo encarte_url) ou lista de URLs (campo active_encartes_urls) das imagens.
    """
    return consultar_encarte()

@tool
def get_pending_suggestions_tool(telefone: str) -> str:
    """
    [RECUPERAR SUGESTÕES PENDENTES]
    Use quando o cliente responder 'sim', 'pode', 'quero' para confirmar produtos sugeridos anteriormente.
    
    Retorna os produtos que foram sugeridos na última busca, com EAN, nome e preço.
    Após recuperar, você DEVE chamar add_item_tool para cada produto.
    
    Args:
        telefone: Número do cliente
    
    Returns:
        Lista de produtos pendentes no formato JSON, ou mensagem de erro.
    """
    from tools.redis_tools import get_suggestions, clear_suggestions
    import json
    
    suggestions = get_suggestions(telefone)
    
    if not suggestions:
        return "❌ Nenhuma sugestão pendente encontrada. Peça ao cliente para especificar o que deseja."
    
    # Limpar cache após recuperar (para não repetir)
    clear_suggestions(telefone)
    
    # Formatar para o agente
    output = "✅ PRODUTOS PENDENTES RECUPERADOS (ADICIONE COM add_item_tool):\n"
    for prod in suggestions:
        output += f"- Nome: {prod.get('nome')}\n  Preço: R$ {prod.get('preco', 0):.2f}\n\n"
    
    return output

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
        return "❌ Carrinho vazio. Não é possível calcular total."
    
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
def finalizar_pedido_tool(cliente: str, telefone: str, endereco: str, forma_pagamento: str, observacao: str = "", comprovante: str = "", taxa_entrega: float = 0.0) -> str:
    """
    Finalizar o pedido usando os itens que estão no carrinho.
    Use quando o cliente confirmar que quer fechar a compra.
    
    Args:
    - cliente: Nome do cliente
    - telefone: Telefone (com DDD)
    - endereco: Endereço de entrega completo
    - forma_pagamento: Pix, Cartão ou Dinheiro
    - observacao: Observações extras (troco, etc)
    - comprovante: URL do comprovante PIX (se houver)
    - taxa_entrega: Valor da taxa de entrega em reais (opcional, padrão 0)
    """
    import json as json_lib
    
    items = get_cart_items(telefone)
    if not items:
        return "❌ O carrinho está vazio! Adicione produtos antes de finalizar."
    
    comprovante_salvo = get_comprovante(telefone)
    comprovante_final = comprovante or comprovante_salvo or ""
    
    total = 0.0
    itens_formatados = []
    
    for item in items:
        preco = item.get("preco", 0.0)
        quantidade = item.get("quantidade", 1.0)
        unidades = item.get("unidades", 0)
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
    
    if taxa_entrega > 0:
        itens_formatados.append({
            "nome_produto": "TAXA DE ENTREGA",
            "quantidade": 1,
            "preco_unitario": round(taxa_entrega, 2),
            "observacao": ""
        })
        total += taxa_entrega
        
    payload = {
        "nome_cliente": cliente,
        "telefone": telefone,
        "endereco": endereco or "A combinar",
        "forma": forma_pagamento,
        "observacao": observacao or "",
        "comprovante_pix": comprovante_final or None,
        "itens": itens_formatados
    }
    
    json_body = json_lib.dumps(payload, ensure_ascii=False)
    
    result = pedidos(json_body)
    
    if "sucesso" in result.lower() or "✅" in result:
        # NÃO LIMPAR O CARRINHO AQUI!
        # O carrinho deve persistir por 15 minutos (TTL do Redis) para permitir alterações.
        # clear_cart(telefone) -> REMOVIDO
        
        # O comprovante pode ser limpo ou não? Melhor manter por segurança, mas o pedido já foi.
        # clear_comprovante(telefone) -> REMOVIDO (TTL cuida disso)
        
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

@tool
def calculadora_tool(expressao: str) -> str:
    """
    Calculadora simples para operações matemáticas gerais.
    Use SEMPRE para conferir cálculos antes de informar valores ao cliente.
    Ex: '4 * 2.29' (resultado: 9.16), '15.99 + 3.00' (resultado: 18.99)
    """
    try:
        # Sanitização básica (permitir apenas math)
        allowed = set("0123456789.+-*/() ")
        if not all(c in allowed for c in expressao):
            return "❌ Caracteres inválidos na expressão."
        
        # Eval seguro após sanitização
        resultado = eval(expressao, {"__builtins__": None}, {})
        return f"🔢 {expressao} = {resultado:.2f}"
    except Exception as e:
        return f"❌ Erro: {str(e)}"

# ============================================
# Listas de Ferramentas por Agente
# ============================================

VENDEDOR_TOOLS = [
    # ean_tool_alias, -> Removido: Use busca_analista (Analista)
    # estoque_preco_alias, -> Removido: Use busca_analista (Analista)
    busca_analista_tool,
    # estoque_tool, -> (Já estava encapsulado na busca_analista, confirmando remoção completa do acesso direto)
    reset_pedido_tool,
    add_item_tool,
    view_cart_tool,
    remove_item_tool,
    consultar_encarte_tool,
    get_pending_suggestions_tool,  # Memória compartilhada com Analista
    time_tool,
    search_history_tool,
    calculadora_tool,  # Para cálculos precisos de valores
]

CAIXA_TOOLS = [
    view_cart_tool,
    calcular_total_tool,
    finalizar_pedido_tool,
    salvar_endereco_tool,
    time_tool,
    calculadora_tool,  # Para conferir valores
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
            google_api_key=settings.google_api_key,
            temperature=temperature,
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

def orchestrator_node(state: AgentState) -> dict:
    """
    Nó Orquestrador: Classifica a intenção e roteia para o agente correto.
    Usa um prompt ultra-leve (~150 tokens).
    """
    logger.info("🧠 [ORCHESTRATOR] Analisando intenção...")
    
    llm = _build_fast_llm()
    prompt = load_prompt("orchestrator.md")
    
    last_user_message = ""
    recent_lines = []
    for msg in reversed(state["messages"]):
        if isinstance(msg, HumanMessage):
            content = msg.content if isinstance(msg.content, str) else str(msg.content)
            content = re.sub(r'\[TELEFONE_CLIENTE:.*?\]', '', content)
            content = re.sub(r'\[HORÁRIO_ATUAL:.*?\]', '', content)
            content = re.sub(r'\[URL_IMAGEM:.*?\]', '', content)
            content = content.strip()
            if content:
                if not last_user_message:
                    last_user_message = content
                recent_lines.append(f"Cliente: {content}")
        elif isinstance(msg, AIMessage):
            if hasattr(msg, 'tool_calls') and msg.tool_calls:
                continue
            content = msg.content if isinstance(msg.content, str) else str(msg.content)
            content = re.sub(r'\[TELEFONE_CLIENTE:.*?\]', '', content)
            content = re.sub(r'\[HORÁRIO_ATUAL:.*?\]', '', content)
            content = re.sub(r'\[URL_IMAGEM:.*?\]', '', content)
            content = content.strip()
            if content:
                recent_lines.append(f"Agente: {content}")
        if len(recent_lines) >= 8:
            break
    
    recent_lines = list(reversed(recent_lines))
    conversation = "\n".join(recent_lines).strip()
    
    if not conversation and not last_user_message:
        logger.warning("⚠️ [ORCHESTRATOR] Nenhuma mensagem do usuário encontrada")
        return {"intent": "vendas", "current_agent": "vendedor"}
    
    user_payload = conversation if conversation else last_user_message
    
    messages = [
        SystemMessage(content=prompt),
        HumanMessage(content=user_payload)
    ]
    
    try:
        response = llm.invoke(messages)
        intent_raw = response.content.strip().lower()
        
        # Normalizar resposta
        if "checkout" in intent_raw or "caixa" in intent_raw:
            intent = "checkout"
        else:
            intent = "vendas"
        
        logger.info(f"🧠 [ORCHESTRATOR] Intenção detectada: {intent} (raw: '{intent_raw}')")
        
        new_agent = "caixa" if intent == "checkout" else "vendedor"
        
        return {
            "intent": intent,
            "current_agent": new_agent
        }
        
    except Exception as e:
        logger.error(f"❌ [ORCHESTRATOR] Erro: {e}")
        return {"intent": "vendas", "current_agent": "vendedor"}


def vendedor_node(state: AgentState) -> dict:
    """
    Nó Vendedor: Agente especializado em vendas com prompt completo.
    """
    logger.info("👩‍💼 [VENDEDOR] Processando...")
    
    # Definir telefone no contexto para memória compartilhada com Analista
    set_current_phone(state["phone"])
    
    prompt = load_prompt("vendedor.md")
    llm = _build_llm(temperature=0.0)  # Temperatura 0 para seguir regras do prompt
    
    # Criar agente ReAct com as ferramentas do vendedor
    agent = create_react_agent(llm, VENDEDOR_TOOLS, prompt=prompt)
    
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

        response_lower_local = (agent_response or "").lower()
        hallucination_detected_local = False
        hallucination_reason_local = ""

        if "adicionei" in response_lower_local or "adicionado" in response_lower_local:
            if "add_item_tool" not in tools_called_local:
                hallucination_detected_local = True
                hallucination_reason_local = "disse 'adicionei' sem chamar add_item_tool"

        if "encontrei" in response_lower_local and "busca_analista" not in tools_called_local:
            if "get_pending_suggestions_tool" not in tools_called_local:
                hallucination_detected_local = True
                hallucination_reason_local = "disse 'encontrei' sem buscar"

        return hallucination_detected_local, hallucination_reason_local, tools_called_local

    result = agent.invoke({"messages": state["messages"]}, config)
    response = _extract_response(result)

    hallucination_detected, hallucination_reason, tools_called = _check_hallucination(result, response)
    if hallucination_detected:
        logger.warning(f"⚠️ ALUCINAÇÃO DETECTADA: {hallucination_reason}. Tools usadas: {tools_called}")

        retry_instruction = SystemMessage(
            content=(
                "RETRY INTERNO (não mostrar ao cliente): sua última resposta foi inválida.\n"
                "- Se você afirmar que adicionou itens, você DEVE chamar add_item_tool.\n"
                "- Se você afirmar que encontrou produtos, você DEVE chamar busca_analista (ou get_pending_suggestions_tool).\n"
                "- Refaça o processamento do pedido e CHAME as ferramentas necessárias.\n"
                "- Não peça desculpas nem mencione erro técnico. Retorne apenas a resposta final ao cliente."
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


def caixa_node(state: AgentState) -> dict:
    """
    Nó Caixa: Agente especializado em checkout com prompt enxuto.
    """
    logger.info("💰 [CAIXA] Processando...")
    
    prompt = load_prompt("caixa.md")
    llm = _build_llm(temperature=0.0)  # Cálculos precisos
    
    # Criar agente ReAct com as ferramentas do caixa
    agent = create_react_agent(llm, CAIXA_TOOLS, prompt=prompt)
    
    # Configuração
    config = {
        "configurable": {"thread_id": state["phone"]},
        "recursion_limit": 15  # Limite menor, operações mais simples
    }
    
    def _check_cashier_hallucination(agent_result: dict, agent_response: str) -> tuple[bool, str, set]:
        messages_local = agent_result.get("messages", []) if isinstance(agent_result, dict) else []
        tools_called_local = set()
        for msg in messages_local:
            if isinstance(msg, AIMessage) and hasattr(msg, "tool_calls") and msg.tool_calls:
                for call in msg.tool_calls:
                    tools_called_local.add(call["name"])

        response_lower_local = (agent_response or "").lower()
        hallucination_detected_local = False
        hallucination_reason_local = ""

        confirmacao_words = ["pedido confirmado", "pedido enviado", "pedido finalizado", "✅ pedido"]
        if any(w in response_lower_local for w in confirmacao_words):
            if "finalizar_pedido_tool" not in tools_called_local:
                hallucination_detected_local = True
                hallucination_reason_local = "disse 'pedido confirmado' sem chamar finalizar_pedido_tool"

        import re
        total_match = re.search(r"total[:\s]*r\$\s*\d+", response_lower_local)
        if total_match and "calcular_total_tool" not in tools_called_local and "finalizar_pedido_tool" not in tools_called_local:
            hallucination_detected_local = True
            hallucination_reason_local = "mencionou total sem calcular"

        return hallucination_detected_local, hallucination_reason_local, tools_called_local

    result = agent.invoke({"messages": state["messages"]}, config)
    response = _extract_response(result)

    hallucination_detected, hallucination_reason, tools_called = _check_cashier_hallucination(result, response)
    if hallucination_detected:
        logger.warning(f"⚠️ ALUCINAÇÃO CAIXA: {hallucination_reason}. Tools: {tools_called}")

        retry_instruction = SystemMessage(
            content=(
                "RETRY INTERNO (não mostrar ao cliente): sua última resposta foi inválida.\n"
                "- Se você afirmar que confirmou/finalizou o pedido, você DEVE chamar finalizar_pedido_tool.\n"
                "- Se você mencionar total (R$), você DEVE chamar calcular_total_tool (ou finalizar_pedido_tool).\n"
                "- Refaça o processamento e CHAME as ferramentas necessárias.\n"
                "- Não peça desculpas nem mencione erro técnico. Retorne apenas a resposta final ao cliente."
            )
        )
        retry_messages = list(state["messages"]) + [retry_instruction]
        retry_result = agent.invoke({"messages": retry_messages}, config)
        retry_response = _extract_response(retry_result)
        retry_hallucination, retry_reason, retry_tools = _check_cashier_hallucination(retry_result, retry_response)
        if not retry_hallucination and retry_response:
            result = retry_result
            response = retry_response
            response_lower = response.lower()
        else:
            logger.warning(f"⚠️ ALUCINAÇÃO CAIXA (RETRY): {retry_reason}. Tools: {retry_tools}")
            response = "Desculpe, tive um problema ao processar. Vou verificar seu pedido novamente..."
            response_lower = response.lower()
    
    # Verificar se o cliente quer voltar ao vendedor
    if "para alterar itens" in response_lower or "mudar o pedido" in response_lower:
        logger.info("💰 [CAIXA] Cliente quer alterar → Devolvendo para Orquestrador")
        return {
            "final_response": response,
            "current_agent": "orchestrator",
            "messages": result.get("messages", [])[-1:] if result.get("messages") else []
        }
    
    logger.info(f"💰 [CAIXA] Resposta: {response[:100]}...")
    
    return {
        "final_response": response,
        "messages": result.get("messages", [])[-1:] if result.get("messages") else []
    }


def _extract_response(result: dict) -> str:
    """Extrai a resposta textual do resultado do agente."""
    if not result or "messages" not in result:
        return "Desculpe, tive um problema. Pode repetir?"
    
    messages = result["messages"]
    
    for msg in reversed(messages):
        if not isinstance(msg, AIMessage):
            continue
        
        if hasattr(msg, 'tool_calls') and msg.tool_calls:
            continue
        
        content = msg.content if isinstance(msg.content, str) else str(msg.content)
        
        if not content or not content.strip():
            continue
        
        if content.strip().startswith(("[", "{")):
            continue
        
        return content
    
    return "Desculpe, não consegui processar. Pode repetir?"

# ============================================
# Roteamento
# ============================================

def route_by_intent(state: AgentState) -> Literal["vendedor", "caixa"]:
    """Decide para qual agente rotear baseado na intenção."""
    intent = state.get("intent", "vendas")
    
    if intent == "checkout":
        return "caixa"
    return "vendedor"

def route_from_caixa(state: AgentState) -> Literal["end", "orchestrator"]:
    """
    Decide se o caixa finaliza ou devolve para o orquestrador.
    """
    # Se o nó caixa definiu 'current_agent' como 'orchestrator', voltamos
    current = state.get("current_agent", "caixa")
    if current == "orchestrator":
        return "orchestrator"
    
    return "end"

# ============================================
# Construção do Grafo
# ============================================

def build_multi_agent_graph():
    """Constrói o StateGraph com a arquitetura de 3 agentes."""
    
    graph = StateGraph(AgentState)
    
    # Adicionar nós
    graph.add_node("orchestrator", orchestrator_node)
    graph.add_node("vendedor", vendedor_node)
    graph.add_node("caixa", caixa_node)
    
    # Fluxo: START → Orquestrador
    graph.add_edge(START, "orchestrator")
    
    # Orquestrador decide para onde ir
    graph.add_conditional_edges(
        "orchestrator",
        route_by_intent,
        {
            "vendedor": "vendedor",
            "caixa": "caixa"
        }
    )
    
    # Vendedor termina (mas poderia loopar se quisesse, por enquanto mantemos simples)
    graph.add_edge("vendedor", END)
    
    # Caixa pode terminar ou voltar
    graph.add_conditional_edges(
        "caixa",
        route_from_caixa,
        {
            "end": END,
            "orchestrator": "orchestrator"
        }
    )
    
    # Compilar
    # REMOVIDO CHECKPOINTER: Para evitar vazamento de estado entre sessões (cross-talk).
    # O estado é passado completo via 'messages' (Redis/Postgres) a cada execução.
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
    history_handler = HybridChatMessageHistory(session_id=telefone, redis_ttl=settings.human_takeover_ttl or 900)
    
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
                mensagem_expandida = f"O cliente respondeu '{clean_message}' CONFIRMANDO. Sua mensagem anterior foi: \"{ultima_pergunta_ia}...\". Se você sugeriu produtos, recupere as sugestões pendentes (get_pending_suggestions_tool) e só então adicione os itens confirmados (add_item_tool). Não invente preço."
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
            "current_agent": "orchestrator",
            "intent": "unknown",
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
