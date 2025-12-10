"""
Ferramentas Redis para buffer de mensagens e cooldown
Apenas funcionalidades essenciais mantidas
"""
import redis
from typing import Optional, Dict, List, Tuple
from config.settings import settings
from config.logger import setup_logger

logger = setup_logger(__name__)

# Conexão global com Redis
_redis_client: Optional[redis.Redis] = None
# Buffer local em memória (fallback quando Redis não está disponível)
_local_buffer: Dict[str, List[str]] = {}


def get_redis_client() -> Optional[redis.Redis]:
    """
    Retorna a conexão com o Redis (singleton)
    """
    global _redis_client
    
    if _redis_client is None:
        try:
            _redis_client = redis.Redis(
                host=settings.redis_host,
                port=settings.redis_port,
                db=settings.redis_db,
                password=settings.redis_password if settings.redis_password else None,
                decode_responses=True,
                socket_connect_timeout=5,
                socket_timeout=5
            )
            # Testar conexão
            _redis_client.ping()
            logger.info(f"Conectado ao Redis: {settings.redis_host}:{settings.redis_port}")
        
        except redis.exceptions.ConnectionError as e:
            logger.error(f"Erro ao conectar ao Redis: {e}")
            _redis_client = None
        
        except Exception as e:
            logger.error(f"Erro inesperado ao conectar ao Redis: {e}")
            _redis_client = None
    
    return _redis_client


# ============================================
# Buffer de mensagens (concatenação por janela)
# ============================================

def buffer_key(telefone: str) -> str:
    """Retorna a chave da lista de buffer de mensagens no Redis."""
    return f"msgbuf:{telefone}"


def push_message_to_buffer(telefone: str, mensagem: str, ttl_seconds: int = 300) -> bool:
    """
    Empilha a mensagem recebida em uma lista no Redis para o telefone.

    - Usa `RPUSH` para adicionar ao final da lista `msgbuf:{telefone}`.
    - Define TTL na primeira inserção (mantém janela de expiração de 5 minutos).
    """
    client = get_redis_client()
    if client is None:
        # Fallback em memória
        msgs = _local_buffer.get(telefone)
        if msgs is None:
            _local_buffer[telefone] = [mensagem]
        else:
            msgs.append(mensagem)
        logger.info(f"[fallback] Mensagem empilhada em memória para {telefone}")
        return True

    key = buffer_key(telefone)
    try:
        client.rpush(key, mensagem)
        # Se não houver TTL, definir um TTL padrão para evitar lixo acumulado
        if client.ttl(key) in (-1, -2):  # -2 = key não existe, -1 = sem TTL
            client.expire(key, ttl_seconds)
        logger.info(f"Mensagem empilhada no buffer: {key}")
        return True
    except redis.exceptions.RedisError as e:
        logger.error(f"Erro ao empilhar mensagem no Redis: {e}")
        return False


def get_buffer_length(telefone: str) -> int:
    """Retorna o tamanho atual do buffer de mensagens para o telefone."""
    client = get_redis_client()
    if client is None:
        # Fallback em memória
        msgs = _local_buffer.get(telefone) or []
        return len(msgs)
    try:
        return int(client.llen(buffer_key(telefone)))
    except redis.exceptions.RedisError as e:
        logger.error(f"Erro ao consultar tamanho do buffer: {e}")
        return 0


def pop_all_messages(telefone: str) -> list[str]:
    """
    Obtém todas as mensagens do buffer e limpa a chave.
    """
    client = get_redis_client()
    if client is None:
        # Fallback em memória
        msgs = _local_buffer.get(telefone) or []
        _local_buffer.pop(telefone, None)
        logger.info(f"[fallback] Buffer consumido para {telefone}: {len(msgs)} mensagens")
        return msgs
    key = buffer_key(telefone)
    try:
        pipe = client.pipeline()
        pipe.lrange(key, 0, -1)
        pipe.delete(key)
        msgs, _ = pipe.execute()
        msgs = [m for m in (msgs or []) if isinstance(m, str)]
        logger.info(f"Buffer consumido para {telefone}: {len(msgs)} mensagens")
        return msgs
    except redis.exceptions.RedisError as e:
        logger.error(f"Erro ao consumir buffer: {e}")
        return []


# ============================================
# Cooldown do agente (pausa de automação)
# ============================================

def cooldown_key(telefone: str) -> str:
    """Chave do cooldown no Redis."""
    return f"cooldown:{telefone}"


def set_agent_cooldown(telefone: str, ttl_seconds: int = 60) -> bool:
    """
    Define uma chave de cooldown para o telefone, pausando a automação.

    - Armazena valor "1" com TTL (padrão 60s).
    """
    client = get_redis_client()
    if client is None:
        # Fallback: não há persistência real, apenas log
        logger.warning(f"[fallback] Cooldown não persistido (Redis indisponível) para {telefone}")
        return False
    try:
        key = cooldown_key(telefone)
        client.set(key, "1", ex=ttl_seconds)
        logger.info(f"Cooldown definido para {telefone} por {ttl_seconds}s")
        return True
    except redis.exceptions.RedisError as e:
        logger.error(f"Erro ao definir cooldown: {e}")
        return False


def is_agent_in_cooldown(telefone: str) -> Tuple[bool, int]:
    """
    Verifica se há cooldown ativo e retorna (ativo, ttl_restante).
    """
    client = get_redis_client()
    if client is None:
        return (False, -1)
    try:
        key = cooldown_key(telefone)
        val = client.get(key)
        if val is None:
            return (False, -1)
        ttl = client.ttl(key)
        ttl = ttl if isinstance(ttl, int) else -1
        return (True, ttl)
    except redis.exceptions.RedisError as e:
        logger.error(f"Erro ao consultar cooldown: {e}")
        return (False, -1)


# ============================================
# Gerenciamento de Sessão de Pedidos
# ============================================

import json
from datetime import datetime

# Constantes de tempo (em segundos)
SESSION_TTL = 40 * 60  # 40 minutos para montar pedido
MODIFICATION_TTL = 15 * 60  # 15 minutos para alterar após envio


def order_session_key(telefone: str) -> str:
    """Chave da sessão de pedido no Redis."""
    return f"order_session:{telefone}"


def get_order_session(telefone: str) -> Optional[Dict]:
    """
    Retorna a sessão de pedido atual do cliente.
    
    Returns:
        Dict com campos:
        - status: 'building' (montando) ou 'sent' (enviado)
        - started_at: timestamp de início
        - sent_at: timestamp de envio (se enviado)
        - order_id: ID do pedido (se enviado)
    """
    client = get_redis_client()
    if client is None:
        return None
    
    try:
        key = order_session_key(telefone)
        data = client.get(key)
        if data:
            return json.loads(data)
        return None
    except Exception as e:
        logger.error(f"Erro ao obter sessão de pedido: {e}")
        return None


def start_order_session(telefone: str) -> bool:
    """
    Inicia uma nova sessão de pedido (status: building).
    TTL de 40 minutos.
    """
    client = get_redis_client()
    if client is None:
        return False
    
    try:
        key = order_session_key(telefone)
        session = {
            "status": "building",
            "started_at": datetime.now().isoformat(),
            "sent_at": None,
            "order_id": None
        }
        client.set(key, json.dumps(session), ex=SESSION_TTL)
        logger.info(f"📦 Nova sessão de pedido iniciada para {telefone} (TTL: {SESSION_TTL//60}min)")
        return True
    except Exception as e:
        logger.error(f"Erro ao iniciar sessão de pedido: {e}")
        return False


def mark_order_sent(telefone: str, order_id: str = None) -> bool:
    """
    Marca o pedido como enviado. 
    Atualiza TTL para 15 minutos (janela de alteração).
    """
    client = get_redis_client()
    if client is None:
        return False
    
    try:
        key = order_session_key(telefone)
        session = get_order_session(telefone)
        
        if session is None:
            session = {"started_at": datetime.now().isoformat()}
        
        session["status"] = "sent"
        session["sent_at"] = datetime.now().isoformat()
        session["order_id"] = order_id
        
        client.set(key, json.dumps(session), ex=MODIFICATION_TTL)
        logger.info(f"✅ Pedido marcado como enviado para {telefone} (TTL modificação: {MODIFICATION_TTL//60}min)")
        return True
    except Exception as e:
        logger.error(f"Erro ao marcar pedido como enviado: {e}")
        return False


def clear_order_session(telefone: str) -> bool:
    """Remove a sessão de pedido."""
    client = get_redis_client()
    if client is None:
        return False
    
    try:
        client.delete(order_session_key(telefone))
        logger.info(f"🗑️ Sessão de pedido removida para {telefone}")
        return True
    except Exception as e:
        logger.error(f"Erro ao limpar sessão de pedido: {e}")
        return False


def get_order_context(telefone: str) -> str:
    """
    Retorna o contexto de pedido para injetar no agente.
    
    Returns:
        String com instrução para o agente baseada no estado da sessão.
    """
    client = get_redis_client()
    session = get_order_session(telefone)
    
    # Chave para rastrear se cliente já teve pedido recente
    history_key = f"order_history:{telefone}"
    
    if session is None:
        # Verificar se tinha sessão anterior (expirou)
        had_previous = False
        if client:
            try:
                had_previous = client.get(history_key) is not None
            except:
                pass
        
        # Iniciar nova sessão
        start_order_session(telefone)
        
        # Marcar que cliente tem histórico (TTL de 2 horas)
        if client:
            try:
                client.set(history_key, "1", ex=7200)  # 2 horas
            except:
                pass
        
        if had_previous:
            # Sessão expirou - avisar o agente
            return "[SESSÃO] Sessão anterior expirou (40min). Novo pedido iniciado. Avise o cliente que o pedido anterior não foi finalizado e pergunte se quer começar um novo."
        else:
            # Conversa nova
            return "[SESSÃO] Nova conversa. Monte o pedido normalmente."
    
    status = session.get("status", "building")
    
    if status == "building":
        # Ainda montando pedido - renovar TTL
        refresh_session_ttl(telefone)
        return ""
    
    elif status == "sent":
        # Pedido já foi enviado - está na janela de modificação
        return "[SESSÃO] Pedido já enviado. Se cliente quiser adicionar algo, use alterar_tool."
    
    return ""


def check_can_modify_order(telefone: str) -> Tuple[bool, str]:
    """
    Verifica se o cliente pode modificar o pedido.
    
    Returns:
        (pode_modificar, mensagem_explicativa)
    """
    session = get_order_session(telefone)
    
    if session is None:
        return (False, "Nenhum pedido ativo. Será criado um novo.")
    
    status = session.get("status", "building")
    
    if status == "building":
        return (True, "Pedido ainda em montagem.")
    
    elif status == "sent":
        # Está na janela de 15min (Redis ainda tem a chave)
        return (True, "Pedido enviado recentemente. Pode alterar com alterar_tool.")
    
    return (False, "Sessão expirada. Novo pedido será criado.")


def refresh_session_ttl(telefone: str) -> bool:
    """
    Renova o TTL da sessão quando o cliente interage (se ainda em building).
    """
    client = get_redis_client()
    if client is None:
        return False
    
    try:
        session = get_order_session(telefone)
        if session and session.get("status") == "building":
            key = order_session_key(telefone)
            client.expire(key, SESSION_TTL)
            logger.debug(f"TTL da sessão renovado para {telefone}")
            return True
        return False
    except Exception as e:
        logger.error(f"Erro ao renovar TTL da sessão: {e}")
        return False