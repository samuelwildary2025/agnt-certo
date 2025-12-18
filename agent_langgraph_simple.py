"""
Agente de IA para Atendimento de Supermercado usando LangGraph
Versão com suporte a VISÃO e Pedidos com Comprovante
"""

from typing import Dict, Any, TypedDict, Sequence, List
import re
from langchain_openai import ChatOpenAI
from langchain_core.messages import BaseMessage, SystemMessage, HumanMessage, AIMessage
from langchain_core.tools import tool
from langchain_core.runnables import RunnableConfig
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from langchain_community.callbacks import get_openai_callback
from langgraph.graph import StateGraph, END
from langgraph.prebuilt import ToolNode, tools_condition, create_react_agent
from langgraph.checkpoint.memory import MemorySaver
from pathlib import Path
import json
import os

from config.settings import settings
from config.logger import setup_logger
from tools.http_tools import estoque, pedidos, alterar, ean_lookup, estoque_preco
from tools.time_tool import get_current_time, search_message_history
from tools.redis_tools import (
    mark_order_sent, 
    add_item_to_cart, 
    get_cart_items, 
    remove_item_from_cart, 
    clear_cart
)
from memory.limited_postgres_memory import LimitedPostgresChatMessageHistory

logger = setup_logger(__name__)

# ============================================
# Definição das Ferramentas (Tools)
# ============================================

@tool
def estoque_tool(url: str) -> str:
    """
    Consultar estoque e preço atual dos produtos no sistema do supermercado.
    Ex: 'https://.../api/produtos/consulta?nome=arroz'
    """
    return estoque(url)

@tool
def add_item_tool(telefone: str, produto: str, quantidade: float = 1.0, observacao: str = "", preco: float = 0.0) -> str:
    """
    Adicionar um item ao carrinho de compras do cliente.
    USAR IMEDIATAMENTE quando o cliente demonstrar intenção de compra.
    """
    item = {
        "produto": produto,
        "quantidade": quantidade,
        "observacao": observacao,
        "preco": preco
    }
    import json as json_lib
    if add_item_to_cart(telefone, json_lib.dumps(item, ensure_ascii=False)):
        return f"✅ Item '{produto}' ({quantidade}) adicionado ao carrinho."
    return "❌ Erro ao adicionar item. Tente novamente."

@tool
def view_cart_tool(telefone: str) -> str:
    """
    Ver os itens atuais no carrinho do cliente.
    """
    items = get_cart_items(telefone)
    if not items:
        return "🛒 O carrinho está vazio."
    
    summary = ["🛒 **Carrinho Atual:**"]
    total_estimado = 0.0
    for i, item in enumerate(items):
        qtd = item.get("quantidade", 1)
        nome = item.get("produto", "?")
        obs = item.get("observacao", "")
        preco = item.get("preco", 0.0)
        subtotal = qtd * preco
        total_estimado += subtotal
        
        desc = f"{i+1}. {nome} (x{qtd})"
        if preco > 0:
            desc += f" - R$ {subtotal:.2f}"
        if obs:
            desc += f" [Obs: {obs}]"
        summary.append(desc)
    
    if total_estimado > 0:
        summary.append(f"\n💰 **Total Estimado:** R$ {total_estimado:.2f}")
        
    return "\n".join(summary)

@tool
def remove_item_tool(telefone: str, item_index: int) -> str:
    """
    Remover um item do carrinho pelo número (índice 1-based, como mostrado no view_cart).
    Ex: Para remover o item 1, passe 1.
    """
    # Converter de 1-based para 0-based
    idx = int(item_index) - 1
    if remove_item_from_cart(telefone, idx):
        return f"✅ Item {item_index} removido do carrinho."
    return "❌ Erro ao remover item (índice inválido?)."

@tool
def finalizar_pedido_tool(cliente: str, telefone: str, endereco: str, forma_pagamento: str, observacao: str = "", comprovante: str = "") -> str:
    """
    Finalizar o pedido usando os itens que estão no carrinho.
    Use quando o cliente confirmar que quer fechar a compra.
    
    Args:
    - cliente: Nome do cliente
    - telefone: Telefone (com DDD)
    - endereco: Endereço de entrega (rua, número, bairro)
    - forma_pagamento: PIX, DINHEIRO, CARTAO
    - observacao: Observações do pedido (opcional)
    - comprovante: URL do comprovante (opcional)
    """
    import json as json_lib
    
    # 1. Obter itens do Redis
    items = get_cart_items(telefone)
    if not items:
        return "❌ O carrinho está vazio! Adicione itens antes de finalizar."
    
    # 2. Calcular total e formatar itens para API
    total = 0.0
    itens_formatados = []
    
    for item in items:
        preco = item.get("preco", 0.0)
        quantidade = item.get("quantidade", 1.0)
        total += preco * quantidade
        
        # Formatar item para API (campos corretos)
        itens_formatados.append({
            "nome_produto": item.get("produto", item.get("nome_produto", "Produto")),
            "quantidade": int(quantidade),
            "preco_unitario": preco
        })
        
    # 3. Montar payload do pedido (campos corretos para API)
    payload = {
        "nome_cliente": cliente,
        "telefone": telefone,
        "endereco": endereco or "A combinar",
        "forma": forma_pagamento,
        "observacao": observacao or "",
        "itens": itens_formatados
    }
    
    json_body = json_lib.dumps(payload, ensure_ascii=False)
    
    # 4. Enviar via HTTP
    result = pedidos(json_body)
    
    # 5. Se sucesso, limpar carrinho e marcar status
    if "sucesso" in result.lower() or "✅" in result:
        clear_cart(telefone)
        mark_order_sent(telefone)
        
    return result

@tool
def alterar_tool(telefone: str, json_body: str) -> str:
    """Atualizar o pedido no painel (para pedidos JÁ enviados)."""
    return alterar(telefone, json_body)

@tool
def search_history_tool(telefone: str, keyword: str = None) -> str:
    """Busca mensagens anteriores do cliente com horários."""
    return search_message_history(telefone, keyword)

@tool
def time_tool() -> str:
    """Retorna a data e hora atual."""
    return get_current_time()

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

# Ferramentas ativas
ACTIVE_TOOLS = [
    ean_tool_alias,
    estoque_preco_alias,
    estoque_tool,
    time_tool,
    search_history_tool,
    add_item_tool,
    view_cart_tool,
    remove_item_tool,
    finalizar_pedido_tool,
    alterar_tool,
]

# ============================================
# Funções do Grafo
# ============================================

def load_system_prompt() -> str:
    base_dir = Path(__file__).resolve().parent
    prompt_path = str((base_dir / "prompts" / "agent_system_optimized.md"))
    try:
        text = Path(prompt_path).read_text(encoding="utf-8")
        text = text.replace("{base_url}", settings.supermercado_base_url)
        text = text.replace("{ean_base}", settings.estoque_ean_base_url)
        return text
    except Exception as e:
        logger.error(f"Falha ao carregar prompt: {e}")
        raise

def _build_llm():
    model = getattr(settings, "llm_model", "gpt-4o-mini")
    temp = float(getattr(settings, "llm_temperature", 0.0))
    return ChatOpenAI(model=model, openai_api_key=settings.openai_api_key, temperature=temp)

def create_agent_with_history():
    system_prompt = load_system_prompt()
    llm = _build_llm()
    memory = MemorySaver()
    agent = create_react_agent(llm, ACTIVE_TOOLS, prompt=system_prompt, checkpointer=memory)
    return agent

_agent_graph = None
def get_agent_graph():
    global _agent_graph
    if _agent_graph is None:
        _agent_graph = create_agent_with_history()
    return _agent_graph

# ============================================
# Função Principal
# ============================================

def run_agent_langgraph(telefone: str, mensagem: str) -> Dict[str, Any]:
    """
    Executa o agente. Suporta texto e imagem (via tag [MEDIA_URL: ...]).
    """
    print(f"[AGENT] Telefone: {telefone} | Msg bruta: {mensagem[:50]}...")
    
    # 1. Extrair URL de imagem se houver (Formato: [MEDIA_URL: https://...])
    image_url = None
    clean_message = mensagem
    
    # Regex para encontrar a tag de mídia injetada pelo server.py
    media_match = re.search(r"\[MEDIA_URL:\s*(.*?)\]", mensagem)
    if media_match:
        image_url = media_match.group(1)
        # Remove a tag da mensagem de texto para não confundir o histórico visual
        # Mas mantemos o texto descritivo original
        clean_message = mensagem.replace(media_match.group(0), "").strip()
        if not clean_message:
            clean_message = "Analise esta imagem/comprovante enviada."
        logger.info(f"📸 Mídia detectada para visão: {image_url}")

    # 2. Salvar histórico (User)
    history_handler = None
    try:
        history_handler = get_session_history(telefone)
        history_handler.add_user_message(mensagem)
    except Exception as e:
        logger.error(f"Erro DB User: {e}")

    try:
        agent = get_agent_graph()
        
        # 3. Construir mensagem (Texto Simples ou Multimodal)
        # IMPORTANTE: Injetar telefone no contexto para que o LLM saiba qual usar nas tools
        telefone_context = f"[TELEFONE_CLIENTE: {telefone}]\n\n"
        
        if image_url:
            # Formato multimodal para GPT-4o / GPT-4o-mini
            message_content = [
                {"type": "text", "text": telefone_context + clean_message},
                {
                    "type": "image_url",
                    "image_url": {"url": image_url}
                }
            ]
            initial_message = HumanMessage(content=message_content)
        else:
            initial_message = HumanMessage(content=telefone_context + clean_message)

        initial_state = {"messages": [initial_message]}
        config = {"configurable": {"thread_id": telefone}, "recursion_limit": 100}
        
        logger.info("Executando agente...")
        
        # Contador de tokens
        with get_openai_callback() as cb:
            result = agent.invoke(initial_state, config)
            
            # Cálculo manual de custo (gpt-4o-mini pricing)
            # Input: $0.15 per 1M tokens | Output: $0.60 per 1M tokens
            input_cost = (cb.prompt_tokens / 1_000_000) * 0.15
            output_cost = (cb.completion_tokens / 1_000_000) * 0.60
            total_cost = input_cost + output_cost
            
            # Log de tokens
            logger.info(f"📊 TOKENS - Prompt: {cb.prompt_tokens} | Completion: {cb.completion_tokens} | Total: {cb.total_tokens}")
            logger.info(f"💰 CUSTO: ${total_cost:.6f} USD (Input: ${input_cost:.6f} | Output: ${output_cost:.6f})")
        
        # 4. Extrair resposta
        output = "Desculpe, não entendi."
        if isinstance(result, dict) and "messages" in result:
            messages = result["messages"]
            if messages:
                last = messages[-1]
                output = last.content if isinstance(last.content, str) else str(last.content)
        
        logger.info("✅ Agente executado")
        logger.info(f"💬 RESPOSTA: {output[:200]}{'...' if len(output) > 200 else ''}")
        
        # 5. Salvar histórico (IA)
        if history_handler:
            try:
                history_handler.add_ai_message(output)
            except Exception as e:
                logger.error(f"Erro DB AI: {e}")

        return {"output": output, "error": None}
        
    except Exception as e:
        logger.error(f"Falha agente: {e}", exc_info=True)
        return {"output": "Tive um problema técnico, tente novamente.", "error": str(e)}

def get_session_history(session_id: str) -> LimitedPostgresChatMessageHistory:
    return LimitedPostgresChatMessageHistory(
        connection_string=settings.postgres_connection_string,
        session_id=session_id,
        table_name=settings.postgres_table_name,
        max_messages=settings.postgres_message_limit
    )

run_agent = run_agent_langgraph
