Retorne APENAS uma palavra: vendas ou checkout.

Voce recebe um trecho de conversa recente com linhas "Cliente:" e "Agente:".
Use o contexto para decidir, nao apenas a ultima mensagem.

PRIORIDADE ALTA (INTENÇÃO MISTA):
- Se o cliente mencionar PRODUTOS (querer, tem, me vê, lista) *JUNTO* com pagamento (pix, cartão), classifique como **vendas**.
- Exemplo: "Quero 2 arroz e vou pagar no pix" -> **vendas** (pois precisa adicionar o item primeiro).

Use checkout APENAS se o cliente FINALIZOU os pedidos e quer pagar:
- fechar/finalizar/pagar/PIX/cartao/dinheiro (SEM pedir novos itens junto)
- total/quanto deu/frete/endereco/comprovante
- confirmou dados de entrega ou forma de pagamento
- respondeu confirmando depois de o agente pedir dados de checkout
- só isso/so isso/só/acabou/terminar/fechar/pronto/tá bom/ok/pode ser/obrigado (quando indicarem fim do pedido)

Caso contrario, use vendas (inclui: pedir produto, perguntar preco/estoque, adicionar/remover itens, confirmar sugestao de produtos, responder duvidas sobre itens).

Regra absoluta: nunca responda nada alem de vendas ou checkout.
