# Interface web

Interface HTML, CSS e JavaScript reaproveitada do projeto Go de referência.
É servida pelo Gateway Python; não precisa de npm nem de compilação.

Na raiz do projeto, execute `.\.venv\Scripts\python.exe -m ecommerce demo`
e abra http://localhost:3001. O backend fornece catálogo, pedidos, interesses
e notificações SSE. `checkout.html` é servido pelo Mock Pagamento.

`?demo=1` ativa a antiga simulação exclusiva do navegador, herdada da referência;
para testar o backend Python, use a URL sem esse parâmetro.

Os testes do adaptador HTTP podem ser executados com:

```powershell
node --test frontend/js/api.test.mjs
```
