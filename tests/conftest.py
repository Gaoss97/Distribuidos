import pytest

from ecommerce import config
from ecommerce.events import generate_keys


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(config, 'KEYS', tmp_path / 'keys')
    generate_keys(config.KEYS)
    monkeypatch.setattr(config, 'CONFIRM_SECONDS', .02)
    monkeypatch.setattr(config, 'DELIVERY_SECONDS', .01)
    monkeypatch.setattr(config, 'PROMOTION_SECONDS', 3600)
    monkeypatch.setattr(config, 'MOCK_PUBLIC_URL', 'http://testserver')
    monkeypatch.setenv('RESEND_API_KEY', '')
    # Só o arquivo de interesses muda. Os catálogos originais continuam somente leitura.
    from ecommerce.services import Promocoes
    original = Promocoes.__init__

    def init(self, bus, path=None):
        original(self, bus, path or tmp_path / 'interesses.json')

    monkeypatch.setattr(Promocoes, '__init__', init)
    return tmp_path
