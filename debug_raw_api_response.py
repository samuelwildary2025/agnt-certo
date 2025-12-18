
import sys
import os
import requests
import json

# Add project root to path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '.')))

from config.settings import settings

def get_auth_headers():
    return {
        "Authorization": settings.supermercado_auth_token,
        "Accept": "application/json",
        "Content-Type": "application/json"
    }

def check_raw_response(product_name="arroz"):
    # Tenta URL do settings, se for a dummy, tenta a do README
    url = f"{settings.supermercado_base_url}/produtos/consulta?nome={product_name}"
    if "api.supermercado.com" in settings.supermercado_base_url:
        print("⚠️ URL de settings parece inválida. Tentando URL do README...")
        base = "https://wildhub-wildhub-sistema-supermercado.5mos1l.easypanel.host/api"
        url = f"{base}/produtos/consulta?nome={product_name}"

    print(f"📡 Consultando API (RAW): {url}")
    
    try:
        # Headers precisam de token, vamos assumir que o token no settings é o correto ou tentar sem
        headers = get_auth_headers()
        # Se o token for placeholder, avisa
        if "seu_token" in headers["Authorization"]:
             print("⚠️ Token de autorização parece inválido (placeholder).")

        response = requests.get(url, headers=headers, timeout=10)
        response.raise_for_status()
        data = response.json()
        
        # Converte para string para contar tamanho
        raw_text = json.dumps(data, indent=2, ensure_ascii=False)
        chars = len(raw_text)
        # Estimativa aproximada: 1 token ~= 4 caracteres
        tokens_est = chars / 4
        
        print(f"\n📦 RESULTADO BRUTO (Sem filtro):")
        print(f"-" * 40)
        print(f"Total de Produtos: {len(data) if isinstance(data, list) else 1}")
        print(f"Tamanho total: {chars} caracteres")
        print(f"Estimativa de Tokens: ~{int(tokens_est)} tokens")
        print(f"-" * 40)
        
        print("\n🔎 AMOSTRA DO JSON (Primeiro item):")
        if isinstance(data, list) and len(data) > 0:
            print(json.dumps(data[0], indent=2, ensure_ascii=False))
        else:
            print(raw_text[:2000] + "...")
            
    except Exception as e:
        print(f"❌ Erro: {e}")

if __name__ == "__main__":
    check_raw_response()
