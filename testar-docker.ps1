$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
$relatorio = Join-Path $PSScriptRoot 'resultados-docker.log'
$OutputEncoding = [System.Text.UTF8Encoding]::new($false)

function Invoke-Docker {
    param([string[]]$Argumentos, [string]$Entrada, [switch]$PermitirFalha)
    # Converte a saida nativa em texto para inclui-la na transcricao do PS 5.1.
    $preferenciaAnterior = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        if ($PSBoundParameters.ContainsKey('Entrada')) {
            $Entrada | & docker @Argumentos 2>&1 | ForEach-Object { Write-Host $_.ToString() }
        } else {
            & docker @Argumentos 2>&1 | ForEach-Object { Write-Host $_.ToString() }
        }
        $codigoSaida = $LASTEXITCODE
    } finally {
        $ErrorActionPreference = $preferenciaAnterior
    }
    if ($codigoSaida -ne 0 -and -not $PermitirFalha) {
        throw "Docker terminou com codigo ${codigoSaida}: $($Argumentos -join ' ')"
    }
}

Start-Transcript -LiteralPath $relatorio -Force
$falhou = $false
try {
    Invoke-Docker -Argumentos @('version')
    # Aguarda todos os servicos estarem saudaveis, incluindo Estoque.
    Invoke-Docker -Argumentos @('compose', 'up', '--build', '-d', '--wait', '--wait-timeout', '180')
    $codigo = Get-Content -LiteralPath (Join-Path $PSScriptRoot 'tests\docker_smoke.py') -Raw -Encoding UTF8
    Invoke-Docker -Argumentos @('compose', 'exec', '-T',
        '-e', 'GATEWAY_TEST_URL=http://principal:3001',
        '-e', 'MOCK_TEST_URL=http://mock-pagamento:8083',
        '-e', 'PAYMENT_TEST_URL=http://pagamento:8082',
        '-e', 'STOCK_TEST_URL=http://estoque:3000', 'principal', 'python', '-') -Entrada $codigo
    Invoke-Docker -Argumentos @('compose', 'exec', '-T', 'principal', 'python', '-m', 'ecommerce', 'adulterador')
    Write-Host 'Testes concluidos. O teste consome uma unidade de P01.'
} catch {
    $falhou = $true
    Write-Host "FALHA: $($_.Exception.Message)"
} finally {
    Write-Host '--- Estado e logs dos conteineres ---'
    try {
        Invoke-Docker -Argumentos @('compose', 'ps', '-a') -PermitirFalha
        Invoke-Docker -Argumentos @('compose', 'logs', '--no-color', '--tail', '150') -PermitirFalha
    } finally {
        Stop-Transcript
    }
}
if ($falhou) {
    throw "Teste falhou. O diagnostico completo esta em $relatorio"
}
Write-Host "Resultado salvo em $relatorio"
