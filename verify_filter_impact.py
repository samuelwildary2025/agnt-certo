
import json
from typing import Dict, Any

# SIMULAÇÃO: Payload "sujo" comum de APIs de ERP (baseado em padrões reais)
# Imagine 10 produtos assim na resposta.
MOCK_RESPONSE_ITEM = {
    "id": 12345,
    "produto": "ARROZ TIO JOAO 1KG",
    "nome_reduzido": "ARROZ TJ 1KG",
    "descricao": "ARROZ TIPO 1 BRANCO TIO JOAO PACOTE 1KG",
    "ean": "7891234567890",
    "ncm": "1006.30.21",
    "cest": "17.001.00",
    "cfop": 5102,
    "situacao_tributaria": "00",
    "aliquota_icms": 18.00,
    "aliquota_pis": 1.65,
    "aliquota_cofins": 7.60,
    "preco": 6.99,
    "preco_custo": 4.50, # Campo interno que não deveria vazar
    "margem_lucro": 35.0, # Campo interno
    "estoque": 150.0,
    "estoque_minimo": 10.0,
    "data_cadastro": "2023-01-01T10:00:00",
    "ultima_alteracao": "2024-12-14T10:00:00",
    "ativo": True,
    "imagem_url": "https://bucket.s3.amazonaws.com/produtos/arroz.jpg",
    "categoria": {
        "id": 5,
        "nome": "CEREAIS",
        "setor": 1
    },
    "fornecedor": {
        "id": 99,
        "nome": "DISTRIBUIDORA ALIMENTOS SA",
        "cnpj": "00.000.000/0001-00"
    }
}

# Lógica de filtro (Copiada do tools/http_tools.py)
def _filter_product(prod: Dict[str, Any]) -> Dict[str, Any]:
    keys_to_keep = [
        "id", "produto", "nome", "descricao", 
        "preco", "preco_venda", "valor", "valor_unitario",
        "estoque", "quantidade", "saldo", "disponivel"
    ]
    clean = {}
    for k, v in prod.items():
        if k.lower() in keys_to_keep or any(x in k.lower() for x in ["preco", "valor", "estoque"]):
                # Ignora campos de imposto/fiscal mesmo se tiver palavras chave
            if any(x in k.lower() for x in ["trib", "ncm", "fiscal", "custo", "margem"]):
                continue
            clean[k] = v
    return clean

def run_simulation():
    # Simula uma resposta com 5 produtos
    full_data = [MOCK_RESPONSE_ITEM for _ in range(5)]
    
    # Payload Antes
    json_before = json.dumps(full_data, indent=2)
    len_before = len(json_before)
    
    # Aplica filtro
    filtered_data = [_filter_product(p) for p in full_data]
    json_after = json.dumps(filtered_data, indent=2)
    len_after = len(json_after)
    
    # Cálculo
    savings = 1 - (len_after / len_before)
    
    print("\n📊 RELATÓRIO DE ECONOMIA (Simulação)")
    print("="*40)
    print(f"📦 Tamanho Original (5 itens): {len_before} caracteres")
    print(f"🧹 Tamanho Filtrado:          {len_after} caracteres")
    print(f"💰 Redução:                   {savings*100:.1f}%")
    print("="*40)
    
    print("\n🔍 O QUE O GPT VÊ AGORA:")
    print(json_after)

if __name__ == "__main__":
    run_simulation()
