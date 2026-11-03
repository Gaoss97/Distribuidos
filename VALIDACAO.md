# Validação da entrega — 02/11/2026

- 16 testes Python aprovados: fluxos de compra, recusa, cancelamento, estoque,
  assinatura, validação, autorização de webhook e interesses.
- 13 testes JavaScript aprovados para o adaptador HTTP da interface.
- Compilação sintática dos módulos Python aprovada.
- `docker compose config --quiet` aprovado.
- Aplicação iniciada com Uvicorn e verificada por HTTP real: página inicial,
  JavaScript, catálogo de nove produtos, documentação e endpoint de saúde.
- Compra completa por HTTP real, com espera da janela inicial de 10 segundos,
  criação de checkout, aprovação e envio com nota fiscal e rastreio.
- Conexão SSE real recebeu, em sequência: pedido.criado, pedido.estoque_ok,
  pagamento.criado, pagamento.aprovado e pedido.enviado.
- Servidores temporários usados na verificação foram encerrados.

Os testes de fluxo usaram o transporte local em memória, com assinatura RSA
real e as mesmas classes de domínio usadas pelos serviços distribuídos.
Não foi executado teste de integração com RabbitMQ: o Docker Desktop não
estava em execução. A configuração Compose foi validada, mas a construção da
imagem e a troca de mensagens entre contêineres ainda precisam ser verificadas
com `docker compose up --build -d` e uma compra pela interface.

Não foi realizado envio real pela Resend, nem teste visual automatizado no
navegador. A interface original foi preservada, com ajustes para respeitar a
janela de cancelamento informada pelo backend Python.

O TestClient emitiu um aviso de descontinuação relacionado ao uso de httpx na
versão instalada do Starlette. O aviso não impediu a execução dos 16 testes.

## Ajuste após a primeira execução com Docker

O teste executado no computador do usuário recebeu HTTP 502 em GET /produtos.
O roteiro original aguardava apenas Gateway e Mock e não verificava a prontidão
do Estoque. Foram adicionados healthchecks para todos os serviços, dependências
de prontidão do Gateway e espera pelo catálogo no teste de ponta a ponta.
O roteiro agora coleta estado e logs dos contêineres mesmo quando falha e
captura a saída dos comandos nativos na transcrição do PowerShell 5.1.

A hipótese de corrida na inicialização precisa ser confirmada pela nova execução.
O ambiente do agente continua sem acesso à conexão local do Docker; portanto,
não foi possível ler os logs dos contêineres nem repetir a integração diretamente.
A configuração Compose e a sintaxe PowerShell foram validadas localmente.

## Resultado da segunda execução com Docker e correção do roteiro

O relatório enviado confirmou todos os contêineres saudáveis e aprovação de
cancelamento inicial, pagamento recusado, repetição do resultado recusado,
devolução de estoque e indisponibilidade sem reserva parcial.
O teste parou ao enviar POST /webhooks/pagamento ao Gateway (HTTP 405).
Esse endpoint pertence ao microsserviço Pagamento, e não ao Principal.
Foi corrigido o roteiro para usar PAYMENT_TEST_URL=http://pagamento:8082,
inclusive verificando a prontidão desse endereço antes de começar.
O fluxo aprovado, a entrega e a demonstração de adulteração ainda precisam
ser confirmados pela próxima execução distribuída do roteiro atualizado.
