"""Contrato de eventos e assinatura RSA-2048 / SHA-256 / PKCS#1 v1.5.

Como no original, a assinatura cobre o JSON de data. Os serviços Python usam
serialização canônica; interoperabilidade binária com o Go não é presumida.
"""
import base64
import json
from datetime import datetime, timezone
from uuid import uuid4

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric import padding, rsa

SERVICES = ('principal', 'estoque', 'pagamento', 'entrega', 'promocoes')
OWNERS = {
    'pedido.criado': 'principal', 'pedido.excluido': 'principal',
    'interesse.promocao': 'principal', 'cancelar.interesse': 'principal',
    'pedido.estoque_ok': 'estoque', 'estoque.indisponivel': 'estoque',
    'pagamento.criado': 'pagamento', 'pagamento.aprovado': 'pagamento',
    'pagamento.recusado': 'pagamento', 'pedido.enviado': 'entrega',
    **{f'promocao.categoria.{c}': 'promocoes' for c in 'ABC'},
}
QUEUES = {
    'principal': [('eCommerce', t) for t in (
        'pedido.estoque_ok', 'estoque.indisponivel', 'pagamento.criado',
        'pagamento.aprovado', 'pagamento.recusado', 'pedido.enviado')],
    'estoque': [('eCommerce', 'pedido.criado'), ('eCommerce', 'pedido.excluido')],
    'pagamento': [('eCommerce', 'pedido.estoque_ok')],
    'entrega': [('eCommerce', 'pagamento.aprovado')],
    'promocoes': [('Promocoes', 'interesse.promocao'),
                  ('Promocoes', 'cancelar.interesse'),
                  ('Promocoes', 'promocao.categoria.*')],
    'c1': [('Promocoes', 'promocao.categoria.A'), ('Promocoes', 'promocao.categoria.B')],
    'c2': [('Promocoes', 'promocao.categoria.*')],
}


def exchange_for(event_type):
    return 'Promocoes' if event_type.startswith(('promocao.', 'interesse.', 'cancelar.')) else 'eCommerce'


def canonical(data):
    return json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()


def now():
    return datetime.now(timezone.utc).isoformat()


def generate_keys(directory):
    """Cria apenas pares ausentes. Nunca substitui uma chave existente."""
    directory.mkdir(parents=True, exist_ok=True)
    for name in SERVICES:
        private = directory / name / 'private_key.pem'
        public = directory / name / 'public_key.pem'
        private.parent.mkdir(exist_ok=True)
        if private.exists():
            key = serialization.load_pem_private_key(private.read_bytes(), password=None)
        else:
            key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
            private.write_bytes(key.private_bytes(serialization.Encoding.PEM,
                serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
            private.chmod(0o600)
        if not public.exists():
            public.write_bytes(key.public_key().public_bytes(serialization.Encoding.PEM,
                serialization.PublicFormat.SubjectPublicKeyInfo))


class Signer:
    def __init__(self, name, directory):
        self.name = name
        path = directory / name / 'private_key.pem'
        self.private = serialization.load_pem_private_key(path.read_bytes(), password=None) if name in SERVICES else None
        producers = {OWNERS[t] for t in OWNERS
                     if any(ex == exchange_for(t) and matches(pattern, t) for ex, pattern in QUEUES.get(name, []))}
        self.public = {n: serialization.load_pem_public_key(
            (directory / n / 'public_key.pem').read_bytes()) for n in producers}

    def envelope(self, event_type, data):
        if OWNERS.get(event_type) != self.name or self.private is None:
            raise ValueError('produtor não autorizado para este evento')
        signature = self.private.sign(canonical(data), padding.PKCS1v15(), hashes.SHA256())
        return {'event_id': str(uuid4()), 'event_type': event_type, 'producer': self.name,
                'occurred_at': now(), 'data': data, 'Signature': base64.b64encode(signature).decode()}

    def verify(self, envelope, routing_key):
        try:
            if envelope['event_type'] != routing_key or OWNERS.get(routing_key) != envelope['producer']:
                return False
            if not envelope['event_id'] or not isinstance(envelope['data'], dict):
                return False
            self.public[envelope['producer']].verify(base64.b64decode(envelope['Signature'], validate=True),
                canonical(envelope['data']), padding.PKCS1v15(), hashes.SHA256())
            return True
        except (KeyError, ValueError, TypeError, InvalidSignature):
            return False


def matches(pattern, routing_key):
    a, b = pattern.split('.'), routing_key.split('.')
    return len(a) == len(b) and all(x == '*' or x == y for x, y in zip(a, b))
