import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / '.env')


def env(name, default):
    return os.getenv(name, default)


RABBITMQ_URL = env('RABBITMQ_URL', 'amqp://ecommerce:ecommerce@localhost:5672/')
KEYS = Path(env('KEYS_PATH', str(ROOT / 'keys')))
DATA = Path(env('DATA_PATH', str(ROOT / 'data')))
CONFIRM_SECONDS = float(env('CONFIRM_SECONDS', '10'))
DELIVERY_SECONDS = float(env('DELIVERY_SECONDS', '2'))
PROMOTION_SECONDS = float(env('PROMOTION_SECONDS', '25'))
MOCK_URL = env('MOCK_URL', 'http://127.0.0.1:8083')
MOCK_PUBLIC_URL = env('MOCK_PUBLIC_URL', 'http://localhost:8083')
WEBHOOK_URL = env('PAGAMENTO_WEBHOOK_URL', 'http://127.0.0.1:8082/webhooks/pagamento')
API_TOKEN = env('MOCK_API_TOKEN', 'desenvolvimento-local')
STOCK_URL = env('ESTOQUE_URL', 'http://127.0.0.1:3000')
