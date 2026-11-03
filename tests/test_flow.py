import time

import pytest
from fastapi.testclient import TestClient

from ecommerce import config
from ecommerce.bus import LocalBroker, LocalBus
from ecommerce.services import Estoque, Promocoes
from ecommerce.web import create_app


def wait_order(client, pid, status):
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        order = next(p for p in client.get('/pedidos').json() if p['id'] == pid)
        if order['status'] == status and (status != 'aguardando pagamento' or order['checkout_url']):
            return order
        time.sleep(.02)
    pytest.fail(f'Pedido não chegou a {status}: {order}')


def new_order(client, qty=1):
    response = client.post('/pedidos', json={'cliente': 'Cliente@Example.com',
        'itens': [{'produto_id': 'P01', 'quantidade': qty}]})
    assert response.status_code == 201
    return response.json()


@pytest.mark.parametrize('decision,expected', [('APROVADO', 'enviado'), ('RECUSADO', 'excluído')])
def test_full_checkout(isolated, decision, expected):
    app = create_app(demo=True)
    with TestClient(app) as client:
        assert client.get('/').status_code == 200
        initial = client.get('/produtos').json()[0]['quantidade']
        order = new_order(client)
        order = wait_order(client, order['id'], 'aguardando pagamento')
        assert order['valor_total'] == 199.9
        cid = order['checkout_url'].split('/')[-1]
        assert client.get('/checkout/' + cid).status_code == 200
        assert 'webhook_token' not in client.get('/api/cobrancas/' + cid).json()
        response = client.post('/api/cobrancas/' + cid + '/resultado', json={'status': decision})
        assert response.status_code == 200
        assert response.json()['notificado']
        final = wait_order(client, order['id'], expected)
        time.sleep(.05)
        assert client.get('/produtos').json()[0]['quantidade'] == initial - (decision == 'APROVADO')
        assert client.post('/api/cobrancas/' + cid + '/resultado', json={'status': decision}).status_code == 200
        opposite = 'RECUSADO' if decision == 'APROVADO' else 'APROVADO'
        assert client.post('/api/cobrancas/' + cid + '/resultado', json={'status': opposite}).status_code == 409
        if decision == 'APROVADO':
            assert final['numero_nota_fiscal'] and final['codigo_rastreio']


def test_cancel_before_publish(isolated, monkeypatch):
    monkeypatch.setattr(config, 'CONFIRM_SECONDS', .15)
    app = create_app(demo=True)
    with TestClient(app) as client:
        initial = client.get('/produtos').json()
        order = new_order(client)
        assert client.delete('/pedidos/' + order['id']).status_code == 202
        time.sleep(.2)
        assert wait_order(client, order['id'], 'excluído')['motivo'] == 'usuario'
        assert client.get('/produtos').json() == initial
        assert not any(e['event_type'] == 'pedido.criado' for e in app.state.broker.history)


def test_unavailable_and_no_partial_reservation(isolated):
    with TestClient(create_app(demo=True)) as client:
        initial = client.get('/produtos').json()
        order = new_order(client, 999)
        assert wait_order(client, order['id'], 'excluído')['motivo'] == 'falta_estoque'
        assert client.get('/produtos').json() == initial


@pytest.mark.parametrize('quantity', [0, -1, True, 1.5, '2'])
def test_bad_quantity(isolated, quantity):
    with TestClient(create_app(demo=True)) as client:
        response = client.post('/pedidos', json={'cliente': 'a@b.com',
            'itens': [{'produto_id': 'P01', 'quantidade': quantity}]})
        assert response.status_code == 422


def test_validation_and_authorization(isolated):
    with TestClient(create_app(demo=True)) as client:
        assert client.post('/pedidos', json={'cliente': 'invalido', 'itens': []}).status_code == 422
        assert client.delete('/pedidos/desconhecido').status_code == 404
        assert client.put('/interesses/Z', json={'email': 'a@b.com'}).status_code == 400
        assert client.post('/webhooks/pagamento', json={'pedido_id': '1', 'cobranca_id': '1', 'status': 'APROVADO'}).status_code == 401
        order = new_order(client)
        wait_order(client, order['id'], 'aguardando pagamento')
        assert client.delete('/pedidos/' + order['id']).status_code == 409
        assert len(client.get('/pedidos?email=cliente@example.com').json()) == 1
        assert client.get('/pedidos?email=outro@example.com').json() == []


async def test_duplicate_items_stock_and_release(isolated):
    broker = LocalBroker()
    stock = Estoque(LocalBus('estoque', broker, config.KEYS))
    initial = stock.stock['P01']
    created = {'event_type': 'pedido.criado', 'data': {'pedido_id': '1', 'valor_total': 599.7,
        'itens': [{'produto_id': 'P01', 'quantidade': 1}, {'produto_id': 'P01', 'quantidade': 2}]}}
    await stock.handle(created)
    await stock.handle(created)
    assert stock.stock['P01'] == initial - 3
    deleted = {'event_type': 'pedido.excluido', 'data': {'pedido_id': '1'}}
    await stock.handle(deleted)
    await stock.handle(deleted)
    await stock.handle(created)
    assert stock.stock['P01'] == initial


async def test_interest_persistence_and_selective_email(isolated):
    broker = LocalBroker()
    service = Promocoes(LocalBus('promocoes', broker, config.KEYS))
    delivered = []

    async def email(recipient, promotion, event_id):
        delivered.append(recipient)

    service.email = email
    await service.handle({'event_type': 'interesse.promocao', 'data': {'email': 'a@b.com', 'categoria': 'A'}})
    await service.handle({'event_type': 'interesse.promocao', 'data': {'email': 'c@d.com', 'categoria': 'C'}})
    restored = Promocoes(service.bus)
    assert restored.interests['a@b.com'] == ['A']
    promotion = {'event_id': '1', 'event_type': 'promocao.categoria.A', 'data': {'categoria': 'A'}}
    await service.handle(promotion)
    await service.handle(promotion)
    assert delivered == ['a@b.com']
    await service.handle({'event_type': 'cancelar.interesse', 'data': {'email': 'a@b.com', 'categoria': 'A'}})
    assert 'a@b.com' not in service.interests
