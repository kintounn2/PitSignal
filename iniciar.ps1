param([switch]$Demo)

$ErrorActionPreference = 'Stop'
try {
    Set-Location -LiteralPath $PSScriptRoot
    Write-Host 'PitSignal - checking Python / verificando Python...' -ForegroundColor Cyan

    # Refresh PATH from Windows settings: Explorer may still have an old PATH
    # after Python was installed. Preserve process-only entries as well.
    $pathParts = @(
        [Environment]::GetEnvironmentVariable('Path', 'Machine'),
        [Environment]::GetEnvironmentVariable('Path', 'User'),
        $env:Path
    ) | Where-Object { $_ }
    $env:Path = [Environment]::ExpandEnvironmentVariables(($pathParts -join ';'))

    $candidates = New-Object System.Collections.Generic.List[object]
    foreach ($name in @('py.exe', 'python.exe', 'python3.exe')) {
        foreach ($command in @(Get-Command $name -CommandType Application -All -ErrorAction SilentlyContinue)) {
            $prefix = @()
            if ($name -eq 'py.exe') { $prefix = @('-3') }
            $candidates.Add(@{ Exe = $command.Source; Prefix = $prefix })
        }
    }
    foreach ($launcher in @(
        (Join-Path $env:WINDIR 'py.exe'),
        (Join-Path $env:LOCALAPPDATA 'Programs\Python\Launcher\py.exe'),
        (Join-Path $env:LOCALAPPDATA 'Microsoft\WindowsApps\py.exe')
    )) {
        if (Test-Path -LiteralPath $launcher -PathType Leaf) {
            $candidates.Add(@{ Exe = $launcher; Prefix = @('-3') })
        }
    }
    $pythonFolder = Join-Path $env:LOCALAPPDATA 'Programs\Python'
    if (Test-Path -LiteralPath $pythonFolder) {
        foreach ($folder in @(Get-ChildItem -LiteralPath $pythonFolder -Directory -ErrorAction SilentlyContinue)) {
            $exe = Join-Path $folder.FullName 'python.exe'
            if (Test-Path -LiteralPath $exe -PathType Leaf) {
                $candidates.Add(@{ Exe = $exe; Prefix = @() })
            }
        }
    }

    $selected = $null
    $seen = @{}
    foreach ($candidate in $candidates) {
        $exe = $candidate.Exe
        $prefix = $candidate.Prefix
        $key = $exe + '|' + ($prefix -join ' ')
        if ($seen.ContainsKey($key)) { continue }
        $seen[$key] = $true
        try {
            $probe = & $exe @prefix -c 'import sys; print(sys.version.split()[0]); sys.exit(0 if sys.version_info >= (3,9) else 1)' 2>&1
            if ($LASTEXITCODE -eq 0) {
                $selected = $candidate
                Write-Host ('Python found / encontrado: ' + ($probe -join ' ')) -ForegroundColor Green
                break
            }
        } catch {
            # Try the next real interpreter if this candidate cannot start.
        }
    }
    if ($null -eq $selected) {
        Write-Host 'Python 3.9+ not found / Python 3.9+ nao encontrado.' -ForegroundColor Yellow
        Write-Host 'Not started. Send a screenshot / Nao iniciou. Envie uma foto.'
        Write-Host 'PowerShell: py -3 --version'
        exit 1
    }

    $entry = Join-Path $PSScriptRoot 'painel_web.py'
    if (-not (Test-Path -LiteralPath $entry -PathType Leaf)) {
        throw 'painel_web.py missing. Extract all ZIP files / Extraia todos os arquivos juntos.'
    }
    $exe = $selected.Exe
    $pythonArgs = @($selected.Prefix) + @($entry)
    if ($Demo) { $pythonArgs += '--demo' }
    Write-Host 'Starting dashboard. Keep this window open / Mantenha esta janela aberta.'
    & $exe @pythonArgs
    $appExit = $LASTEXITCODE
    if ($appExit -ne 0) {
        Write-Host ('Python started; dashboard error / Erro do painel: ' + $appExit + '.') -ForegroundColor Yellow
        Write-Host 'Send the error above. Do not reinstall Python / Envie o erro acima.'
    }
    exit $appExit
} catch {
    Write-Host ('Startup failed / Falha ao iniciar: ' + $_.Exception.Message) -ForegroundColor Red
    Write-Host 'Send a screenshot of this window / Envie uma foto desta janela.'
    exit 1
}
