import copy

import pytest

from ecommerce import config
from ecommerce.events import Signer, exchange_for, generate_keys, matches


def test_signature_and_tampering(isolated):
    producer, consumer = Signer('principal', config.KEYS), Signer('estoque', config.KEYS)
    event = producer.envelope('pedido.criado', {'pedido_id': '1', 'itens': []})
    assert consumer.verify(event, 'pedido.criado')
    tampered = copy.deepcopy(event)
    tampered['data']['pedido_id'] = '2'
    assert not consumer.verify(tampered, 'pedido.criado')
    assert not consumer.verify({**event, 'Signature': ''}, 'pedido.criado')
    assert not consumer.verify({**event, 'producer': 'entrega'}, 'pedido.criado')
    assert not consumer.verify(event, 'pedido.excluido')
    assert not consumer.verify({}, 'pedido.criado')


def test_keys_not_overwritten(isolated):
    path = config.KEYS / 'principal' / 'private_key.pem'
    previous = path.read_bytes()
    generate_keys(config.KEYS)
    assert path.read_bytes() == previous


def test_producer_ownership(isolated):
    with pytest.raises(ValueError):
        Signer('estoque', config.KEYS).envelope('pagamento.aprovado', {})


def test_topic_bindings():
    assert matches('promocao.categoria.*', 'promocao.categoria.A')
    assert not matches('promocao.categoria.A', 'promocao.categoria.B')
    assert not matches('promocao.*', 'promocao.categoria.A')
    assert exchange_for('pedido.criado') == 'eCommerce'
    assert exchange_for('interesse.promocao') == 'Promocoes'
