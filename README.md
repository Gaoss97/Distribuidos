# Trabalho 1 — E-commerce distribuído em Python

Reimplementação local baseada em `C:\Users\gabri\Documents\ecommerce-web-app-main`.
O backend Go foi substituído por Python 3.12+, FastAPI e asyncio. O catálogo,
o estoque inicial, o HTML, o CSS, o JavaScript da loja e o checkout foram
reaproveitados do projeto de referência. A interface continua em HTML/CSS/JS;
os serviços, a mensageria, as regras de negócio e a criptografia são Python.

## Iniciar rapidamente no Windows

O ambiente `.venv` já foi preparado nesta máquina. No PowerShell:

```powershell
cd C:\Users\gabri\Documents\distribuidos\trabalho1
.\.venv\Scripts\python.exe -m ecommerce demo
```

Abra **http://localhost:3001**. Encerre com Ctrl+C.
Também pode executar `powershell -ExecutionPolicy Bypass -File .\iniciar.ps1`.

Esse modo funciona sem Docker: os serviços rodam juntos e trocam eventos
assinados por um transporte em memória. É uma demonstração funcional, não
uma execução distribuída real. Nenhum pagamento real é realizado.

Em outra máquina, com Python 3.12 ou mais recente instalado:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\.venv\Scripts\python.exe -m ecommerce demo
```

Se `python` abrir a Microsoft Store, instale Python e habilite sua entrada no
PATH, ou use `py -3.12` no primeiro comando. O ambiente virtual não é portátil;
recrie-o quando mover o projeto para outra máquina.

## Execução distribuída com Docker

Abra o Docker Desktop e, na raiz deste projeto, execute:

```powershell
docker compose up --build -d
docker compose logs -f
```

O Compose sobe RabbitMQ, gera as chaves que estiverem faltando e inicia
Principal, Estoque, Pagamento, Entrega, Promoções, C1, C2 e Mock Pagamento
em processos isolados. A primeira construção requer internet.

- Loja: http://localhost:3001
- Checkout: http://localhost:8083/checkout/ID_DA_COBRANCA
- RabbitMQ: http://localhost:15672 — usuário/senha `ecommerce` / `ecommerce`
- Documentação HTTP do Gateway: http://localhost:3001/docs

Parar: `docker compose down`. As chaves e o RabbitMQ usam volumes próprios.
As portas são vinculadas ao endereço local da máquina. Se outro projeto já
ocupar 5672, 15672, 3001 ou 8083, pare-o antes de iniciar este Compose.

## Executar processos Python separadamente

Para estudar os serviços fora dos contêineres:

```powershell
docker compose up -d rabbitmq
.\.venv\Scripts\python.exe -m ecommerce gerar-chaves
```

Abra um terminal por processo, sempre na raiz do projeto:

```powershell
.\.venv\Scripts\python.exe -m ecommerce estoque
.\.venv\Scripts\python.exe -m ecommerce mock-pagamento
.\.venv\Scripts\python.exe -m ecommerce pagamento
.\.venv\Scripts\python.exe -m ecommerce entrega
.\.venv\Scripts\python.exe -m ecommerce c1
.\.venv\Scripts\python.exe -m ecommerce c2
.\.venv\Scripts\python.exe -m ecommerce promocoes
.\.venv\Scripts\python.exe -m ecommerce principal
```

Cada linha acima representa um terminal diferente. Os comandos aceitam
`--host` e `--port`. As URLs entre serviços podem ser alteradas no `.env`.
Não execute o modo demo e o Principal simultaneamente na mesma porta.

## Demonstrar o fluxo

1. Adicione produtos ao carrinho e informe um e-mail.
2. Finalize a compra. O pedido fica 10 segundos aguardando confirmação;
   nesse período, pode ser cancelado sem publicar `pedido.criado`.
3. O Estoque reserva todos os itens ou nenhum. Falta de estoque exclui o pedido.
4. O Pagamento cria uma cobrança REST no Mock. A URL chega à loja por SSE.
5. Abra o checkout e escolha aprovado ou recusado. O Mock envia um webhook
   autenticado. Recusa exclui o pedido e devolve a reserva; aprovação aciona
   a Entrega, que gera nota fiscal e rastreio depois de 2 segundos.
6. Cadastre interesse nas categorias A, B ou C. Promoções publica ofertas a
   cada 25 segundos; C1 recebe A/B e C2 recebe A/B/C. O envio de e-mails é
   simulado no terminal enquanto `RESEND_API_KEY` estiver vazia.

O cancelamento solicitado pelo usuário é restrito à janela anterior à
publicação. Essa regra é mais restrita que a do backend Go e evita a corrida
entre cancelamento, aprovação do pagamento e expedição. Recusas e falta de
estoque continuam gerando exclusão automática e eventos de compensação.

## E-mails reais (opcional)

Copie `.env.example` para `.env`, configure `RESEND_API_KEY` e um remetente
autorizado em `PROMOCOES_EMAIL_FROM`, e reinicie Promoções. Com chave configurada,
o serviço usa a API Resend e envia somente aos interessados na categoria.
Nenhuma credencial do projeto original foi copiada.

## Eventos e assinatura

`eCommerce` é uma exchange **direct**; `Promocoes` é **topic**. Não há fanout.
As filas usam o prefixo `fila.python.` para não se misturarem às filas do Go.
O início de cada serviço declara toda a topologia, inclusive as filas dos
consumidores ainda ausentes. As filas e exchanges são duráveis, as mensagens
são persistentes e as publicações esperam confirmação do broker. O consumidor
confirma manualmente após processar; falhas transitórias são reenfileiradas.

| Consumidor | Eventos |
|---|---|
| Principal | pedido.estoque_ok, estoque.indisponivel, pagamento.criado, pagamento.aprovado, pagamento.recusado, pedido.enviado |
| Estoque | pedido.criado, pedido.excluido |
| Pagamento | pedido.estoque_ok |
| Entrega | pagamento.aprovado |
| Promoções | interesse.promocao, cancelar.interesse, promocao.categoria.* |
| C1 | promocao.categoria.A, promocao.categoria.B |
| C2 | promocao.categoria.* |

Os interesses e as ofertas usam a exchange `Promocoes`; o restante usa
`eCommerce`. O envelope contém `event_id`, `event_type`, `producer`,
`occurred_at`, `data` e `Signature`. Como no original, a assinatura cobre
**data**, usando RSA-2048, SHA-256 e PKCS#1 v1.5. O consumidor valida assinatura,
produtor autorizado e correspondência entre tipo e routing key antes de agir.
Metadados do envelope não estão cobertos pela assinatura, seguindo o contrato
de referência; este projeto não é um protocolo de segurança para produção.

O JSON assinado tem ordenação canônica entre os serviços Python. Não se presume
compatibilidade de assinatura com a serialização dos serviços Go. As chaves
novas ficam em `keys/<servico>/` e não substituem pares já existentes.

Com RabbitMQ e os serviços executando, a demonstração de adulteração é:

```powershell
.\.venv\Scripts\python.exe -m ecommerce adulterador
```

São publicadas mensagens sem assinatura, com produtor desconhecido e com
conteúdo alterado após assinatura. Os consumidores registram o descarte.

## Testes

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\.venv\Scripts\python.exe -m pytest -q
node --test frontend/js/api.test.mjs
```

Os testes Python cobrem assinatura/adulteração, propriedade dos eventos,
preservação das chaves, compra aprovada e recusada, checkout, devolução do
estoque, cancelamento, indisponibilidade, validação, webhook sem credencial,
itens repetidos e interesses persistidos com destinatários por categoria.
Não precisam de RabbitMQ nem enviam e-mails reais.

## Estrutura

```text
ecommerce/
  __main__.py       comandos para cada processo
  config.py         ambiente e caminhos independentes do diretório corrente
  events.py         envelopes, contratos, chaves e assinatura
  bus.py            transportes RabbitMQ e local
  services.py       regras dos cinco microsserviços e consumidores
  web.py            APIs REST, SSE, checkout e hospedagem do frontend
frontend/           interface original e checkout reaproveitados
data/               catálogo e estoque inicial originais
tests/              testes automatizados
Dockerfile
docker-compose.yml
iniciar.ps1
```

## Limites de persistência

Assim como no original, pedidos, reservas, cobranças, resultados e deduplicação
ficam em memória. Reiniciar um serviço perde esse estado. Filas duráveis não
transformam esse estado em persistente e eventos antigos podem sobreviver ao
reinício. Para uma demonstração limpa, encerre o conjunto e remova as filas
`fila.python.*` pelo painel do RabbitMQ antes de reiniciá-lo. O arquivo
`data/interesses.local.json` é persistente e ignorado pelo Git. Use uma única
instância por serviço; não inicie múltiplos workers do mesmo serviço.

Uma versão de produção exigiria banco por serviço, outbox transacional,
deduplicação persistente, tratamento de mensagens mortas, autenticação de
clientes e isolamento de chaves privadas. Aqui o e-mail é apenas um filtro,
sem login; as rotas devem permanecer no ambiente local de estudo.

## Referências técnicas

- [aio-pika: publicação e consumo assíncronos](https://docs.aio-pika.com/quick-start.html)
- [cryptography: RSA](https://cryptography.io/en/latest/hazmat/primitives/asymmetric/rsa/)
- [FastAPI: ciclo de vida dos serviços](https://fastapi.tiangolo.com/advanced/events/)

O modo distribuído depende de um Docker/RabbitMQ em execução. A validação
realizada nesta entrega está descrita em `VALIDACAO.md`.
