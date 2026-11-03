param([switch]$Distribuido)
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
if ($Distribuido) {
    docker compose up --build -d
    if ($LASTEXITCODE -ne 0) { throw 'Não foi possível iniciar. Verifique se o Docker Desktop está aberto.' }
    Write-Host 'Loja: http://localhost:3001 | RabbitMQ: http://localhost:15672'
    exit
}
$pythonProjeto = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $pythonProjeto)) {
    python -m venv .venv
    if ($LASTEXITCODE -ne 0) { throw 'Instale Python 3.12 ou mais recente e habilite-o no PATH.' }
    & $pythonProjeto -m pip install -r requirements.txt
    if ($LASTEXITCODE -ne 0) { throw 'Falha ao instalar dependências.' }
}
Write-Host 'Abra http://localhost:3001. Para encerrar, pressione Ctrl+C.'
& $pythonProjeto -m ecommerce demo
