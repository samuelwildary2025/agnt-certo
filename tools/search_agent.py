"""Ferramenta de Sub-Agente para Busca Especializada de Produtos"""
import json
import logging
from typing import List, Dict, Any, Optional
from pathlib import Path
from langchain_core.messages import HumanMessage
from langchain_core.tools import tool
from langgraph.prebuilt import create_react_agent
from pydantic.v1 import BaseModel, Field
from langchain_openai import ChatOpenAI
from langchain_google_genai import ChatGoogleGenerativeAI

from config.settings import settings
from config.logger import setup_logger
from tools.vector_search_subagent import run_vector_search_subagent
from tools.http_tools import estoque_preco

logger = setup_logger(__name__)

_ANALISTA_PROMPT_CACHE: Optional[str] = None



def _load_analista_prompt() -> str:
    global _ANALISTA_PROMPT_CACHE
    if _ANALISTA_PROMPT_CACHE is not None:
        return _ANALISTA_PROMPT_CACHE

    base_dir = Path(__file__).resolve().parent.parent
    prompt_path = base_dir / "prompts" / "analista.md"
    _ANALISTA_PROMPT_CACHE = prompt_path.read_text(encoding="utf-8")
    return _ANALISTA_PROMPT_CACHE





@tool("banco_vetorial")
def banco_vetorial_tool(query: str, limit: int = 10) -> str:
    """
    Realiza uma busca vetorial no banco de dados de produtos.
    Retorna uma lista de produtos mais similares semanticamente à query.
    """
    return run_vector_search_subagent(query, limit=limit)


@tool("estoque_preco")
def estoque_preco_tool(ean: str) -> str:
    """
    Consulta o estoque e preço atual de um produto pelo seu código EAN.
    Retorna JSON com dados atualizados.
    """
    return estoque_preco(ean)


@tool("calculadora")
def calculadora_tool(expressao: str) -> str:
    """
    Calculadora simples. Avalia expressões matemáticas básicas.
    Use para calcular quantidade = valor / preco_kg.
    Ex: calculadora("5 / 40") retorna "0.125"
    """
    try:
        # Sanitizar expressão (apenas permitir números e operadores básicos)
        allowed_chars = set("0123456789.+-*/() ")
        if not all(c in allowed_chars for c in expressao):
            return "Erro: Expressão inválida"
        result = eval(expressao)
        return str(round(result, 3))
    except Exception as e:
        return f"Erro: {e}"


def _run_analista_agent_for_term(term: str, telefone: Optional[str] = None) -> dict:
    prompt = _load_analista_prompt()
    
    llm = _get_fast_llm()
    agent = create_react_agent(llm, [banco_vetorial_tool, estoque_preco_tool], prompt=prompt)

    user_payload = json.dumps(
        {"termo": term},
        ensure_ascii=False,
    )

    config = {"recursion_limit": 8}
    if telefone:
        config["configurable"] = {"thread_id": telefone}

    result = agent.invoke({"messages": [HumanMessage(content=user_payload)]}, config)
    messages = result.get("messages", []) if isinstance(result, dict) else []

    for m in reversed(messages):
        if getattr(m, "type", None) != "ai":
            continue
        content = m.content if isinstance(m.content, str) else str(m.content)
        content = (content or "").strip()
        if not content:
            continue
        try:
            return json.loads(content)
        except Exception:
            return {"ok": False, "termo": term, "motivo": "Resposta nao-JSON do analista"}


    return {"ok": False, "termo": term, "motivo": "Sem resposta"}


# TERM_EXTRACTOR_PROMPT REMOVIDO - Simplificação do fluxo
# O Vendedor envia os termos já separados e o Analista resolve

# ============================================
# 2. Configurações do Modelo
# ============================================

_HTTP_CLIENT_CACHE = None
_HTTP_ASYNC_CLIENT_CACHE = None

def _openai_model_supports_temperature(model: str) -> bool:
    m = (model or "").lower().strip()
    if m.startswith("gpt-5") or m.startswith("gpt5") or "gpt-5" in m:
        return False
    return True

def _get_fast_llm():
    """Retorna um modelo rápido e barato para tarefas de sub-agente."""
    global _HTTP_CLIENT_CACHE, _HTTP_ASYNC_CLIENT_CACHE

    # PREFERÊNCIA: Usar o modelo configurado no settings (ex: grok-beta)
    model_name = getattr(settings, "llm_model", "gemini-2.5-flash")
    temp = float(getattr(settings, "llm_temperature", 0.0))

    if settings.llm_provider == "google":
        return ChatGoogleGenerativeAI(
            model=model_name,
            google_api_key=settings.google_api_key,
            temperature=temp
        )
    else:
        client_kwargs = {}
        if settings.openai_api_base:
            client_kwargs["base_url"] = settings.openai_api_base

        import httpx
        
        # Singleton Clients para evitar abrir mil conexões no loop
        if _HTTP_CLIENT_CACHE is None:
            _HTTP_CLIENT_CACHE = httpx.Client(timeout=30.0)
        if _HTTP_ASYNC_CLIENT_CACHE is None:
            _HTTP_ASYNC_CLIENT_CACHE = httpx.AsyncClient(timeout=30.0)
        
        if _openai_model_supports_temperature(model_name):
            return ChatOpenAI(
                model=model_name,
                api_key=settings.openai_api_key,
                temperature=temp,
                http_client=_HTTP_CLIENT_CACHE,
                http_async_client=_HTTP_ASYNC_CLIENT_CACHE,
                **client_kwargs
            )
        return ChatOpenAI(
            model=model_name,
            api_key=settings.openai_api_key,
            http_client=_HTTP_CLIENT_CACHE,
            http_async_client=_HTTP_ASYNC_CLIENT_CACHE,
            **client_kwargs
        )

# ============================================
# 3. Função Principal (Tool)
# ============================================

def analista_produtos_tool(queries_str: str, telefone: str = None) -> str:
    """
    [ANALISTA DE PRODUTOS]
    Agente Especialista que traduz pedidos do cliente em produtos reais do banco de dados.
    Usa busca vetorial + inteligência semântica.
    
    Args:
        queries_str: Termos de busca (ex: "arroz, feijão, pão").
        telefone: Opcional - número do cliente para salvar sugestões no cache.
    """
    results = []
    validated_products = []  # Para cache no Redis
    
    # SIMPLIFICADO: Separação simples por vírgula ou newline (sem LLM intermediário)
    # O Vendedor já envia termos limpos e o Analista resolve o significado
    extracted_terms = [t.strip() for t in queries_str.replace("\n", ",").split(",") if t.strip()]

    mode = "lote" if len(extracted_terms) > 1 else "individual"
    logger.info(f"🕵️ [SUB-AGENT] Modo de busca: {mode} | termos: {extracted_terms}")
    
    # Função helper para processar cada termo em paralelo
    def _process_single_term(term: str):
        try:
            decision = _run_analista_agent_for_term(term, telefone=telefone)
            if not isinstance(decision, dict) or not decision.get("ok"):
                motivo = (decision or {}).get("motivo") if isinstance(decision, dict) else None
                return (f"❌ {term}: {motivo or 'Nao encontrado'}", None)

            # MODO MULTIPLAS OPÇÕES
            opcoes = decision.get("opcoes")
            if opcoes and isinstance(opcoes, list) and len(opcoes) > 0:
                out_lines = [f"📋 [ANALISTA] OPÇÕES PARA '{term}' (Pergunte ao cliente):"]
                for i, opt in enumerate(opcoes, 1):
                    n = opt.get("nome", "Item")
                    p = float(opt.get("preco", 0.0))
                    out_lines.append(f"   {i}. {n} - R$ {p:.2f}")
                
                out_lines.append("\n⚠️ NÃO Adicionado automaticamente. Liste as opções para o cliente.")
                return ("\n".join(out_lines), None)

            # MODO ÚNICO
            nome = str(decision.get("nome") or "").strip()
            preco = float(decision.get("preco") or 0.0)

            if not nome:
                return (f"❌ {term}: Resposta incompleta do analista", None)

            validated_item = {"nome": nome, "preco": preco, "termo_busca": term}
            razao = str(decision.get("razao") or "").strip()
            
            result_str = (
                "🔍 [ANALISTA] ITEM VALIDADO:\n"
                f"- Nome: {nome}\n"
                f"- Preço Tabela: R$ {preco:.2f}\n"
                f"- Obs: {razao}\n"
                f"\n🔔 DICA: Item encontrado com sucesso.\n"
                f"- Se o cliente pediu para COMPRAR/ADICIONAR: use add_item_tool.\n"
                f"- Se o cliente apenas PERGUNTOU PREÇO/TEM: responda apenas com o preço."
            )
            return (result_str, validated_item)
            
        except Exception as e:
            logger.error(f"❌ [SUB-AGENT] Erro no agente Analista para '{term}': {e}")
            return (f"❌ {term}: Erro interno na busca.", None)

    # Execução Paralela
    import concurrent.futures
    
    # Limitar número de workers para não saturar
    max_workers = min(10, len(extracted_terms) + 1)
    
    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
        # Submeter tarefas mantendo a ordem: mapa {future: index}
        future_to_index = {
            executor.submit(_process_single_term, term): i 
            for i, term in enumerate(extracted_terms)
        }
        
        # Array para guardar resultados na ordem correta
        ordered_results = [None] * len(extracted_terms)
        
        for future in concurrent.futures.as_completed(future_to_index):
            index = future_to_index[future]
            try:
                ordered_results[index] = future.result()
            except Exception as e:
                logger.error(f"Erro fatal processando future index {index}: {e}")
                ordered_results[index] = (f"❌ Erro interno.", None)
                
    # Coletar resultados finais
    for res in ordered_results:
        if not res: 
            continue
        res_str, val_item = res
        if res_str:
            results.append(res_str)
        if val_item:
            validated_products.append(val_item)

    # SALVAR CACHE NO REDIS SE TIVER TELEFONE
    if telefone and validated_products:
        try:
            from tools.redis_tools import save_suggestions
            save_suggestions(telefone, validated_products)
            logger.info(f"💾 [SUB-AGENT] Cache salvo: {len(validated_products)} produtos para {telefone}")
        except Exception as e:
            logger.error(f"Erro ao salvar cache de sugestões: {e}")

    if not results:
        return "Nenhum produto encontrado."
        
    return "\n".join(results)
