$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)
$synth = $null
try {
    $request = [Console]::In.ReadToEnd() | ConvertFrom-Json
    $locale = 'pt'
    if ($request.language -eq 'en') { $locale = 'en' }
    $preferred = 'pt-BR'
    if ($locale -eq 'en') { $preferred = 'en-US' }
    Add-Type -AssemblyName System.Speech
    $synth = New-Object System.Speech.Synthesis.SpeechSynthesizer
    $voices = @($synth.GetInstalledVoices() | Where-Object {
        $_.Enabled -and $_.VoiceInfo.Culture.Name -like ($locale + '-*')
    } | Sort-Object @{Expression={if ($_.VoiceInfo.Culture.Name -eq $preferred) {0} else {1}}})
    if ($voices.Count -eq 0) {
        if ($locale -eq 'en') { throw 'No compatible English voice found. Add an English voice in Windows Speech settings, then test again.' }
        throw 'Nenhuma voz em portugues compativel foi encontrada. Adicione uma voz em Portugues nas configuracoes de Fala do Windows e teste novamente.'
    }
    $name = $voices[0].VoiceInfo.Name
    $synth.SelectVoice($name)
    $synth.SetOutputToDefaultAudioDevice()
    $synth.Rate = 0
    $synth.Volume = 90
    $synth.Speak([string]$request.text)
    @{ok=$true; voice=$name} | ConvertTo-Json -Compress
} catch {
    @{ok=$false; error=$_.Exception.Message} | ConvertTo-Json -Compress
    exit 1
} finally {
    if ($null -ne $synth) { $synth.Dispose() }
}
