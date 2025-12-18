"""
Servidor FastAPI para receber mensagens do WhatsApp e processar com o agente
Suporta: Texto, Áudio (Transcrição), Imagem (Visão) e PDF (Extração de Texto + Link)
Versão: 1.5.5 (Correção de LID e Buffer Personalizado)
"""
from fastapi import FastAPI, Request, HTTPException, BackgroundTasks
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from typing import Optional, Dict, Any
import requests
from datetime import datetime
import time
import random
import threading
import re
import io

# Tenta importar pypdf para leitura de comprovantes
try:
    from pypdf import PdfReader
except ImportError:
    PdfReader = None

from config.settings import settings
from config.logger import setup_logger
from agent_langgraph_simple import run_agent_langgraph as run_agent, get_session_history
from tools.redis_tools import (
    push_message_to_buffer,
    get_buffer_length,
    pop_all_messages,
    set_agent_cooldown,
    is_agent_in_cooldown,
    get_order_session,
    start_order_session,
    refresh_session_ttl,
    get_order_context,
)

logger = setup_logger(__name__)

app = FastAPI(title="Agente de Supermercado", version="1.5.5")

# --- Models ---
class WhatsAppMessage(BaseModel):
    telefone: str
    mensagem: str
    message_id: Optional[str] = None
    timestamp: Optional[str] = None
    message_type: Optional[str] = "text"

class AgentResponse(BaseModel):
    success: bool
    response: str
    telefone: str
    timestamp: str
    error: Optional[str] = None

# --- Helpers ---

def get_api_base_url() -> str:
    """Prioriza UAZ_API_URL > WHATSAPP_API_URL."""
    return (settings.uaz_api_url or settings.whatsapp_api_url or "").strip().rstrip("/")

def get_media_url_uaz(message_id: str) -> Optional[str]:
    """Solicita link público da mídia (Imagem/PDF)."""
    if not message_id: return None
    base = get_api_base_url()
    if not base: return None

    try:
        from urllib.parse import urlparse
        parsed = urlparse(base)
        url = f"{parsed.scheme}://{parsed.netloc}/message/download"
    except:
        url = f"{base.split('/message')[0]}/message/download"

    headers = {"Content-Type": "application/json", "token": (settings.whatsapp_token or "").strip()}
    # return_link=True devolve url pública
    payload = {"id": message_id, "return_link": True, "return_base64": False}
    
    try:
        resp = requests.post(url, headers=headers, json=payload, timeout=15)
        if resp.status_code == 200:
            data = resp.json()
            link = data.get("fileURL") or data.get("url")
            if link: return link
    except Exception as e:
        logger.error(f"Erro ao obter link mídia: {e}")
    return None

def process_pdf_uaz(message_id: str) -> Optional[str]:
    """Baixa o PDF e extrai o texto (para leitura do valor)."""
    if not PdfReader:
        logger.error("❌ Biblioteca pypdf não instalada. Adicione ao requirements.txt")
        return "[Erro: sistema não suporta leitura de PDF]"

    url = get_media_url_uaz(message_id)
    if not url: return None
    
    logger.info(f"📄 Processando PDF: {url}")
    try:
        # Baixar o arquivo
        response = requests.get(url, timeout=20)
        response.raise_for_status()
        
        # Ler PDF em memória
        f = io.BytesIO(response.content)
        reader = PdfReader(f)
        
        text_content = []
        for page in reader.pages:
            text_content.append(page.extract_text())
            
        full_text = "\n".join(text_content)
        full_text = re.sub(r'\s+', ' ', full_text).strip()
        
        logger.info(f"✅ PDF lido com sucesso ({len(full_text)} chars)")
        return full_text
        
    except Exception as e:
        logger.error(f"Erro ao ler PDF: {e}")
        return None

def transcribe_audio_uaz(message_id: str) -> Optional[str]:
    """Solicita transcrição de áudio."""
    if not message_id: return None
    base = get_api_base_url()
    if not base: return None

    try:
        from urllib.parse import urlparse
        parsed = urlparse(base)
        url = f"{parsed.scheme}://{parsed.netloc}/message/download"
    except:
        url = f"{base.split('/message')[0]}/message/download"

    headers = {"Content-Type": "application/json", "token": (settings.whatsapp_token or "").strip()}
    payload = {
        "id": message_id, 
        "transcribe": True, 
        "return_link": False, 
        "openai_apikey": settings.openai_api_key
    }
    
    try:
        logger.info(f"🎧 Transcrevendo áudio: {message_id}")
        resp = requests.post(url, headers=headers, json=payload, timeout=25)
        if resp.status_code == 200:
            return resp.json().get("transcription")
    except Exception as e:
        logger.error(f"Erro transcrição: {e}")
    return None

def _extract_incoming(payload: Dict[str, Any]) -> Dict[str, Any]:
    """
    Normaliza e processa (Texto, Áudio, Imagem, Documento/PDF).
    BLINDADA: Ignora LIDs e prioriza números reais.
    """
    
    def _clean_number(jid: Any) -> Optional[str]:
        """Extrai apenas o número de telefone de um JID válido."""
        if not jid or not isinstance(jid, str): return None
        
        # Se tiver @lid, é ID de dispositivo (IGNORAR)
        if "@lid" in jid: return None
        
        # Se tiver @g.us, é grupo (IGNORAR)
        if "@g.us" in jid: return None
        
        # Pega a parte antes do @
        if "@" in jid:
            jid = jid.split("@")[0]
            
        # Remove tudo que não for dígito
        num = re.sub(r"\D", "", jid)
        
        # Validação básica (evita IDs estranhos)
        if len(num) > 15 or len(num) < 10:
            return None
            
        return num

    chat = payload.get("chat") or {}
    message_any = payload.get("message") or {}
    
    if isinstance(payload.get("messages"), list):
        try:
            m0 = payload["messages"][0]
            message_any = m0
            chat = {"wa_id": m0.get("sender") or m0.get("chatid")}
        except: pass

    # --- LÓGICA DE TELEFONE BLINDADA ---
    telefone = None
    
    # Ordem de prioridade para encontrar o número real
    candidates = []
    
    # 1. Sender/ChatID (Geralmente o mais preciso: 5585...@s.whatsapp.net)
    if isinstance(message_any, dict):
        candidates.append(message_any.get("sender"))
        candidates.append(message_any.get("chatid"))
    
    # 2. Objeto Chat
    candidates.append(chat.get("id"))
    candidates.append(chat.get("wa_id"))
    candidates.append(chat.get("phone"))
    
    # 3. Payload Raiz (Menos confiável)
    candidates.append(payload.get("from"))
    candidates.append(payload.get("sender"))

    # Varre a lista e pega o primeiro válido (sem LID)
    for cand in candidates:
        cleaned = _clean_number(cand)
        if cleaned:
            telefone = cleaned
            break
            
    # Fallback de emergência (avisa no log)
    if not telefone and payload.get("from"):
        raw = str(payload.get("from"))
        if "@lid" not in raw:
            telefone = re.sub(r"\D", "", raw)
            logger.warning(f"⚠️ Usando fallback de telefone: {telefone}")

    # --- Extração de Conteúdo ---
    mensagem_texto = payload.get("text")
    message_id = payload.get("id") or payload.get("messageid")
    from_me = False
    
    raw_type = str(message_any.get("messageType") or "").lower()
    media_type = str(message_any.get("mediaType") or "").lower()
    base_type = str(message_any.get("type") or "").lower()
    mimetype = str(message_any.get("mimetype") or "").lower()
    
    message_type = "text"
    if "audio" in raw_type or "ptt" in media_type or "audio" in base_type:
        message_type = "audio"
    elif "image" in raw_type or "image" in media_type or "image" in base_type:
        message_type = "image"
    elif "document" in raw_type or "document" in base_type or "application/pdf" in mimetype:
        message_type = "document"

    if isinstance(message_any, dict):
        message_id = message_any.get("messageid") or message_any.get("id") or message_id
        from_me = bool(message_any.get("fromMe") or message_any.get("wasSentByApi") or False)
        
        content = message_any.get("content")
        if isinstance(content, str) and not mensagem_texto:
            mensagem_texto = content
        elif isinstance(content, dict):
            mensagem_texto = content.get("text") or content.get("caption") or mensagem_texto
        
        if not mensagem_texto:
            txt = message_any.get("text")
            if isinstance(txt, dict):
                mensagem_texto = txt.get("body")
            else:
                mensagem_texto = txt or message_any.get("body")

    if from_me:
        # Se for mensagem enviada por MIM, tenta achar o destinatário
        candidates_me = [chat.get("wa_id"), chat.get("phone"), payload.get("sender")]
        telefone = next((re.sub(r"\D", "", c) for c in candidates_me if c and "@lid" not in str(c)), telefone)

    # --- Lógica de Mídia ---
    if message_type == "audio" and not mensagem_texto:
        if message_id:
            trans = transcribe_audio_uaz(message_id)
            mensagem_texto = f"[Áudio]: {trans}" if trans else "[Áudio inaudível]"
        else:
            mensagem_texto = "[Áudio sem ID]"
            
    elif message_type == "image":
        caption = mensagem_texto or ""
        if message_id:
            url = get_media_url_uaz(message_id)
            if url: 
                mensagem_texto = f"{caption} [MEDIA_URL: {url}]".strip()
            else: 
                mensagem_texto = f"{caption} [Imagem recebida - erro ao baixar]".strip()
        else:
            mensagem_texto = f"{caption} [Imagem recebida]".strip()

    elif message_type == "document":
        if "pdf" in mimetype or (mensagem_texto and ".pdf" in str(mensagem_texto).lower()):
            pdf_url = get_media_url_uaz(message_id) if message_id else None
            pdf_text = ""
            if message_id:
                extracted = process_pdf_uaz(message_id)
                if extracted:
                    pdf_text = f"\n[Conteúdo PDF]: {extracted[:1200]}..."
            
            if pdf_url:
                mensagem_texto = f"Comprovante/PDF Recebido. {pdf_text} [MEDIA_URL: {pdf_url}]"
            else:
                mensagem_texto = f"[PDF sem link] {pdf_text}"

    return {
        "telefone": telefone,
        "mensagem_texto": mensagem_texto,
        "message_type": message_type,
        "message_id": message_id,
        "from_me": from_me,
    }

def send_whatsapp_message(telefone: str, mensagem: str) -> bool:
    base = get_api_base_url()
    if not base: return False
    try:
        from urllib.parse import urlparse
        parsed = urlparse(base)
        url = f"{parsed.scheme}://{parsed.netloc}/send/text"
    except:
        url = f"{base.split('/message')[0]}/send/text"
    
    headers = {"Content-Type": "application/json", "token": (settings.whatsapp_token or "").strip()}
    
    # Configuração de split de mensagens
    # Max 500 chars por mensagem para não enviar textões
    max_len = 500
    msgs = []
    
    if len(mensagem) > max_len:
        # Divide por parágrafos duplos primeiro
        paragrafos = mensagem.split('\n\n')
        curr = ""
        
        for p in paragrafos:
            # Se o parágrafo sozinho é muito grande, divide por quebras simples
            if len(p) > max_len:
                if curr:
                    msgs.append(curr.strip())
                    curr = ""
                # Divide parágrafo grande por linhas
                linhas = p.split('\n')
                for linha in linhas:
                    if len(curr) + len(linha) + 1 <= max_len:
                        curr += linha + "\n"
                    else:
                        if curr: msgs.append(curr.strip())
                        curr = linha + "\n"
            elif len(curr) + len(p) + 2 <= max_len:
                curr += p + "\n\n"
            else:
                if curr: msgs.append(curr.strip())
                curr = p + "\n\n"
        
        if curr: msgs.append(curr.strip())
    else:
        msgs = [mensagem]
    
    try:
        for i, msg in enumerate(msgs):
            payload = {"number": re.sub(r"\D", "", telefone or ""), "text": msg, "openTicket": "1"}
            requests.post(url, headers=headers, json=payload, timeout=10)
            
            # Delay entre mensagens para parecer mais natural (exceto última)
            if i < len(msgs) - 1:
                time.sleep(random.uniform(0.8, 1.5))
                
        return True
    except Exception as e:
        logger.error(f"Erro envio: {e}")
        return False

# --- Presença & Buffer ---
presence_sessions = {}
buffer_sessions = {}

def send_presence(num, type_):
    """Envia status: 'composing' (digitando) ou 'paused'."""
    base = get_api_base_url()
    if not base: return
    try:
        from urllib.parse import urlparse
        parsed = urlparse(base)
        url = f"{parsed.scheme}://{parsed.netloc}/message/presence"
    except:
        url = f"{base}/message/presence"
    try:
        requests.post(url, headers={"Content-Type": "application/json", "token": settings.whatsapp_token}, 
                     json={"number": re.sub(r"\D","",num), "presence": type_}, timeout=5)
    except: pass

def process_async(tel, msg, mid=None):
    """
    Processa mensagem do Buffer.
    Fluxo Humano:
    1. Espera (simula leitura).
    2. Digita (composing).
    3. Processa (IA).
    4. Para de digitar (paused).
    5. Envia.
    """
    try:
        num = re.sub(r"\D", "", tel)
        
        # 1. Simular "Lendo" (Delay Humano)
        tempo_leitura = random.uniform(2.0, 4.0) 
        time.sleep(tempo_leitura)

        # 2. Começar a "Digitar"
        send_presence(num, "composing")
        
        # 3. Processamento IA
        res = run_agent(tel, msg)
        txt = res.get("output", "Erro ao processar.")
        
        # 4. Parar "Digitar"
        send_presence(num, "paused")
        time.sleep(0.5) # Pausa dramática antes de chegar

        # 5. Enviar Mensagem
        send_whatsapp_message(tel, txt)

    except Exception as e:
        logger.error(f"Erro async: {e}")
    finally:
        # Garante limpeza
        send_presence(tel, "paused")
        presence_sessions.pop(re.sub(r"\D", "", tel), None)

def buffer_loop(tel):
    """
    Loop do Buffer (3 ciclos de 5s = 15 segundos)
    Total espera máxima: ~15 segundos
    
    IMPORTANTE: Após processar, verifica se chegaram novas mensagens durante
    a execução do agente e as processa também (evita mensagens "perdidas").
    """
    try:
        n = re.sub(r"\D","",tel)
        
        while True:  # Loop principal para pegar mensagens que chegam durante processamento
            prev = get_buffer_length(n)
            
            # Se não tem mensagens, sair
            if prev == 0:
                break
                
            stall = 0
            
            # Esperar por mais mensagens (3 ciclos de 3.5s)
            while stall < 3:
                time.sleep(5)  # 3 ciclos de 5s = 15 segundos total
                curr = get_buffer_length(n)
                if curr > prev: prev, stall = curr, 0
                else: stall += 1
            
            # Consumir e processar mensagens
            msgs = pop_all_messages(n)
            # Usa ' | ' como separador para o agente entender que são itens/pedidos separados
            final = " | ".join([m for m in msgs if m.strip()])
            
            if not final:
                break
                
            # Obter contexto de sessão
            order_ctx = get_order_context(n)
            if order_ctx:
                final = f"{order_ctx}\n\n{final}"
            
            # Processar (enquanto isso, novas mensagens podem chegar)
            process_async(n, final)
            
            # Após processar, o loop vai verificar se tem novas mensagens
            # Se tiver, processa novamente. Se não, sai.
            
    except Exception as e:
        logger.error(f"Erro no buffer_loop: {e}")
    finally: 
        buffer_sessions.pop(re.sub(r"\D","",tel), None)

# --- Endpoints ---
@app.get("/")
async def root(): return {"status":"online", "ver":"1.5.5"}

@app.get("/health")
async def health(): return {"status":"healthy", "ts":datetime.now().isoformat()}

@app.post("/")
@app.post("/webhook/whatsapp")
async def webhook(req: Request, tasks: BackgroundTasks):
    try:
        pl = await req.json()
        data = _extract_incoming(pl)
        tel, txt, from_me = data["telefone"], data["mensagem_texto"], data["from_me"]

        if not tel or not txt: return JSONResponse(content={"status":"ignored"})
        
        logger.info(f"In: {tel} | {data['message_type']} | {txt[:50]}")

        if from_me:
            # Detectar Human Takeover: Se o número do agente enviou mensagem
            # Ativar cooldown para pausar a IA
            agent_number = (settings.whatsapp_agent_number or "").strip()
            if agent_number:
                # Limpar para comparação
                agent_clean = re.sub(r"\D", "", agent_number)
                # Se a mensagem foi enviada PARA um cliente (não é conversa interna)
                if tel and tel != agent_clean:
                    # Ativar cooldown - IA pausa por X minutos
                    ttl = settings.human_takeover_ttl  # Default: 900s (15min)
                    set_agent_cooldown(tel, ttl)
                    logger.info(f"🙋 Human Takeover ativado para {tel} - IA pausa por {ttl//60}min")
            
            try: get_session_history(tel).add_ai_message(txt)
            except: pass
            return JSONResponse(content={"status":"ignored_self"})

        num = re.sub(r"\D","",tel)
        
        # NOTA: 'send_presence' imediato removido para evitar comportamento robótico.
        # O cliente verá 'digitando' apenas após o buffer, no process_async.

        active, _ = is_agent_in_cooldown(num)
        if active:
            push_message_to_buffer(num, txt)
            return JSONResponse(content={"status":"cooldown"})

        try:
            if not presence_sessions.get(num):
                presence_sessions[num] = True
        except: pass

        if push_message_to_buffer(num, txt):
            if not buffer_sessions.get(num):
                buffer_sessions[num] = True
                threading.Thread(target=buffer_loop, args=(num,), daemon=True).start()
        else:
            tasks.add_task(process_async, tel, txt)

        return JSONResponse(content={"status":"buffering"})
    except Exception as e:
        logger.error(f"Erro webhook: {e}")
        return JSONResponse(status_code=500, detail=str(e))

@app.post("/message")
async def direct_msg(msg: WhatsAppMessage):
    try:
        res = run_agent(msg.telefone, msg.mensagem)
        return AgentResponse(success=True, response=res["output"], telefone=msg.telefone, timestamp="")
    except Exception as e:
        return AgentResponse(success=False, response="", telefone="", error=str(e))

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("server:app", host=settings.server_host, port=settings.server_port, log_level=settings.log_level.lower())
