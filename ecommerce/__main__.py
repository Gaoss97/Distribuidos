import argparse
import asyncio
import logging

import uvicorn

from . import config
from .bus import RabbitBus
from .events import generate_keys


async def adulterate():
    """Publica três mensagens inválidas para cada tipo de evento demonstrado."""
    import aio_pika
    from .events import canonical
    for role, kind in [('principal', 'pedido.criado'), ('pagamento', 'pagamento.aprovado')]:
        bus = RabbitBus(role)
        await bus.start(lambda _: asyncio.sleep(0), consume=False)
        try:
            valid = bus.signer.envelope(kind, {'pedido_id': 'adulterado', 'valor_total': 1, 'itens': []})
            examples = [{**valid, 'Signature': ''}, {**valid, 'producer': 'desconhecido'},
                        {**valid, 'data': {**valid['data'], 'valor_total': 99999}}]
            for event in examples:
                await bus.exchanges['eCommerce'].publish(aio_pika.Message(canonical(event)), routing_key=kind)
        finally:
            await bus.close()


def main():
    parser = argparse.ArgumentParser(description='E-commerce distribuído em Python')
    parser.add_argument('servico', choices=['demo', 'principal', 'estoque', 'pagamento', 'entrega',
        'promocoes', 'c1', 'c2', 'mock-pagamento', 'gerar-chaves', 'adulterador'])
    parser.add_argument('--host', default='127.0.0.1')
    parser.add_argument('--port', type=int)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(name)s: %(message)s')
    if args.servico == 'gerar-chaves':
        generate_keys(config.KEYS)
        print('Chaves disponíveis em', config.KEYS)
        return
    if args.servico == 'adulterador':
        asyncio.run(adulterate())
        return
    ports = {'principal': 3001, 'estoque': 3000, 'pagamento': 8082, 'mock-pagamento': 8083,
             'entrega': 8084, 'promocoes': 8085, 'c1': 8086, 'c2': 8087, 'demo': 3001}
    port = args.port or ports[args.servico]
    demo = args.servico == 'demo'
    if demo:
        config.MOCK_PUBLIC_URL = f'http://localhost:{port}'
    from .web import create_app
    uvicorn.run(create_app(args.servico, demo=demo), host=args.host, port=port, workers=1)


if __name__ == '__main__':
    main()
