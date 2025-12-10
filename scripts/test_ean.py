"""
Teste direto do smart-responder (Supabase Functions) para extrair EANs.
Uso:
  python scripts/test_ean.py "Coca Cola 2L"

Não depende de Pydantic nem das configurações do projeto; lê variáveis do .env
ou do ambiente diretamente.
"""
import os
import sys
import json
import requests


def _fallback_load_env(path: str = ".env"):
    try:
        if not os.path.exists(path):
            return
        with open(path, "r", encoding="utf-8") as f:
            for line in f.read().splitlines():
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())
    except Exception:
        pass


def _auth_header():
    token = (os.environ.get("SMART_RESPONDER_AUTH") or os.environ.get("SMART_RESPONDER_TOKEN") or "").strip()
    if not token:
        return None
    return token if token.lower().startswith("bearer ") else f"Bearer {token}"


def _extract_pairs_from_text(text: str):
    import re
    eans = re.findall(r'"codigo_ean"\s*:\s*([0-9]+)', text)
    names = re.findall(r'"produto"\s*:\s*"([^"]+)"', text)
    pairs = []
    limit = min(len(eans), len(names)) or max(len(eans), len(names))
    for i in range(min(limit, 50)):
        e = eans[i] if i < len(eans) else None
        n = names[i] if i < len(names) else None
        if e or n:
            pairs.append((e, n))
    return pairs


def _walk_extract(payload):
    pairs = []
    def try_obj(d: dict):
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

    def walk(obj):
        if isinstance(obj, dict):
            try_obj(obj)
            for _, v in obj.items():
                walk(v)
        elif isinstance(obj, list):
            for it in obj:
                walk(it)
        elif isinstance(obj, str):
            pairs.extend(_extract_pairs_from_text(obj))

    walk(payload)
    return pairs


def main():
    # carregar env
    try:
        from dotenv import load_dotenv
        load_dotenv()
    except Exception:
        _fallback_load_env()

    if len(sys.argv) < 2:
        print("Uso: python scripts/test_ean.py \"<consulta de produto>\"")
        sys.exit(1)

    query = " ".join(sys.argv[1:]).strip()
    url = (os.environ.get("SMART_RESPONDER_URL") or "").strip().replace("`", "")
    auth = _auth_header()
    apikey = (os.environ.get("SMART_RESPONDER_APIKEY") or "").strip()

    if not url or not auth:
        print("Erro: SMART_RESPONDER_URL/AUTH não configurados no .env")
        sys.exit(2)

    headers = {
        "Authorization": auth,
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    if apikey:
        headers["apikey"] = apikey

    payload = {"query": query}
    print(f"\nConsultando smart-responder: {url}\nQuery: {query}\n")
    try:
        resp = requests.post(url, headers=headers, json=payload, timeout=15)
        txt = resp.text
        try:
            data = resp.json()
            pairs = _walk_extract(data)
        except Exception:
            pairs = _extract_pairs_from_text(txt)

        if pairs:
            print("EANS encontrados:")
            for i, (e, n) in enumerate(pairs[:10], 1):
                if e and n:
                    print(f"{i}) {e} - {n}")
                elif e:
                    print(f"{i}) {e}")
                elif n:
                    print(f"{i}) {n}")
            print("\n✅ Comunicação com Supabase OK")
        else:
            print("⚠️ Nenhum EAN encontrado. Verifique o retorno da Function e a consulta.")
        # preview truncado
        preview = txt if isinstance(txt, str) else json.dumps(txt, ensure_ascii=False)
        if isinstance(preview, str) and len(preview) > 2000:
            preview = preview[:2000] + "\n... (truncado)"
        print("\nRetorno bruto:\n")
        print(f"\n[INFO] Tamanho da resposta bruta: {len(txt)} caracteres")
        print(preview)
    except requests.exceptions.RequestException as e:
        print(f"Erro ao consultar smart-responder: {e}")


if __name__ == "__main__":
    main()