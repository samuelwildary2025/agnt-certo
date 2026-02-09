import json
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Dict, List, Optional

from config.logger import setup_logger
from tools.db_vector_search import run_vector_search
from tools.http_tools import estoque_preco
from tools.redis_tools import save_suggestions


logger = setup_logger(__name__)


def _extract_eans_and_names(vector_output: str) -> List[Dict[str, str]]:
    lines = [l.strip() for l in (vector_output or "").splitlines() if l.strip()]
    results: List[Dict[str, str]] = []
    for line in lines:
        if ") " not in line:
            continue
        parts = line.split(" - ")
        if len(parts) < 2:
            continue
        ean_part = parts[0].split(") ", 1)[-1].strip()
        ean_match = re.search(r"\b(\d{2,})\b", ean_part)
        ean = ean_match.group(1) if ean_match else None
        if not ean:
            ean_match = re.search(r"\bean\s*(\d{2,})\b", line, flags=re.IGNORECASE)
            ean = ean_match.group(1) if ean_match else None
        if not ean:
            continue
        name = " - ".join(parts[1:-1]).strip() if len(parts) > 2 else parts[1].strip()
        results.append({"ean": ean, "nome": name})
    return results


def _fetch_stock(ean: str) -> Optional[Dict[str, Any]]:
    raw = estoque_preco(ean)
    try:
        data = json.loads(raw)
    except Exception:
        return None
    if isinstance(data, list) and data:
        item = data[0]
        if isinstance(item, dict):
            return item
    return None


def _build_options(term: str, limit: int = 15) -> List[Dict[str, Any]]:
    vector_output = run_vector_search(term, limit=limit)
    candidates = _extract_eans_and_names(vector_output)
    if not candidates:
        return []

    results_by_ean: Dict[str, Dict[str, Any]] = {}
    with ThreadPoolExecutor(max_workers=8) as executor:
        future_map = {executor.submit(_fetch_stock, c["ean"]): c for c in candidates[:limit]}
        for future in as_completed(future_map):
            candidate = future_map[future]
            item = future.result()
            if not item:
                continue
            nome = item.get("produto") or candidate.get("nome") or ""
            preco = item.get("preco")
            qtd = item.get("qtd_produto")
            if preco is None:
                continue
            # FRIGORIFICO e HORTI-FRUTI: sempre disponíveis (estoque controlado diferente)
            categoria1 = str(item.get("classificacao01") or "").strip().upper()
            is_always_available = categoria1 in ("FRIGORIFICO", "HORTI-FRUTI")
            if not is_always_available and qtd is not None and float(qtd) <= 0:
                continue
            categoria_parts = [
                str(item.get("classificacao01") or "").strip(),
                str(item.get("classificacao02") or "").strip(),
                str(item.get("classificacao03") or "").strip(),
            ]
            categoria_parts = [p for p in categoria_parts if p]
            categoria = " / ".join(categoria_parts) if categoria_parts else None
            option: Dict[str, Any] = {
                "nome": nome,
                "preco": preco,
                "qtd_produto": qtd,
            }
            if categoria:
                option["categoria"] = categoria
            results_by_ean[candidate.get("ean")] = option
    options: List[Dict[str, Any]] = []
    for candidate in candidates[:limit]:
        ean = candidate.get("ean")
        if ean in results_by_ean:
            options.append(results_by_ean[ean])
    return options


def _format_price(value: float) -> str:
    """Formata preço no padrão brasileiro."""
    return f"R$ {value:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def _format_options_list(options: List[Dict[str, Any]], termo: str, limit: int = 5) -> str:
    """Gera uma lista formatada de produtos para o cliente."""
    if not options:
        return ""
    
    lines = [f"📋 **Encontrei para '{termo}':**"]
    for opt in options[:limit]:
        nome = opt.get("nome", "Produto")
        preco = opt.get("preco", 0)
        lines.append(f"• {nome} - {_format_price(preco)}")
    
    return "\n".join(lines)


def analista_produtos_tool(produtos: str, telefone: str = "") -> str:
    raw = (produtos or "").strip()
    if not raw:
        return json.dumps({"ok": False, "motivo": "Nenhum produto informado"}, ensure_ascii=False)

    terms = [t.strip() for t in raw.split(",") if t.strip()]
    
    # Busca única ou múltipla
    if len(terms) <= 1:
        options = _build_options(raw, limit=15)
        
        if not options:
            return json.dumps({
                "ok": False, 
                "termo": raw, 
                "motivo": "Nenhum produto similar encontrado com preço ativo"
            }, ensure_ascii=False)
        
        # Salvar sugestões para memória compartilhada
        if telefone:
            save_suggestions(telefone, [{"nome": o["nome"], "preco": o["preco"], "termo_busca": raw} for o in options])
        
        # Organizar resposta: melhor opção + lista formatada
        melhor = options[0]  # Primeiro resultado é o mais relevante
        lista = _format_options_list(options, raw, limit=5)
        
        return json.dumps({
            "ok": True, 
            "termo": raw, 
            "melhor_opcao": {"nome": melhor["nome"], "preco": melhor["preco"]},
            "lista_formatada": lista,
            "opcoes": options[:5]  # Limitar a 5 para não sobrecarregar
        }, ensure_ascii=False)

    # Múltiplos termos (busca em lote)
    items = []
    with ThreadPoolExecutor(max_workers=min(8, len(terms))) as executor:
        future_map = {executor.submit(_build_options, term, 15): term for term in terms}
        for future in as_completed(future_map):
            term = future_map[future]
            options = future.result()
            
            if telefone and options:
                save_suggestions(telefone, [{"nome": o["nome"], "preco": o["preco"], "termo_busca": term} for o in options])
            
            # Para cada termo, retornar a melhor opção + lista
            if options:
                melhor = options[0]
                lista = _format_options_list(options, term, limit=3)
                items.append({
                    "termo": term, 
                    "melhor_opcao": {"nome": melhor["nome"], "preco": melhor["preco"]},
                    "lista_formatada": lista,
                    "opcoes": options[:3]
                })
            else:
                items.append({
                    "termo": term, 
                    "melhor_opcao": None,
                    "lista_formatada": f"❌ Não encontrei '{term}'",
                    "opcoes": []
                })

    # Gerar resumo consolidado para múltiplos itens
    linhas_resumo = ["📋 **Produtos encontrados:**"]
    for item in items:
        if item.get("melhor_opcao"):
            nome = item["melhor_opcao"]["nome"]
            preco = item["melhor_opcao"]["preco"]
            linhas_resumo.append(f"• {nome} - {_format_price(preco)}")
        else:
            linhas_resumo.append(f"• ❌ {item['termo']} - não encontrado")
    
    resumo = "\n".join(linhas_resumo)

    return json.dumps({
        "ok": True, 
        "resumo_formatado": resumo,
        "itens": items
    }, ensure_ascii=False)

