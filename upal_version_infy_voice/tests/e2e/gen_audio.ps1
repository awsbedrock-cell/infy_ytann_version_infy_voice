# gen_audio.ps1 — synthesize the voice-test WAVs Chrome feeds via fake capture.
Add-Type -AssemblyName System.Speech
$out = Join-Path $PSScriptRoot "audio"
New-Item -ItemType Directory -Force $out | Out-Null

function Synth($ssml, $path, $voice, $rate) {
    $s = New-Object System.Speech.Synthesis.SpeechSynthesizer
    if ($voice) { $s.SelectVoice($voice) }
    $s.Rate = $rate
    # Chrome fake-audio-capture requires 16kHz 16-bit mono PCM
    $fmt = New-Object System.Speech.AudioFormat.SpeechAudioFormatInfo(
        16000, [System.Speech.AudioFormat.AudioBitsPerSample]::Sixteen,
        [System.Speech.AudioFormat.AudioChannel]::Mono)
    $s.SetOutputToWaveFile($path, $fmt)
    $s.SpeakSsml($ssml)
    $s.Dispose()
    Write-Output "wrote $path"
}

# test1: start -> english question -> infy stop (mid-answer). Slower speech,
# "in fee" so Web Speech hears the wake word cleanly.
$ssml1 = @"
<speak version='1.0' xml:lang='en-US'>
  in fee start
  <break time='10s'/>
  how are winds formed
  <break time='9s'/>
  in fee stop
  <break time='8s'/>
</speak>
"@
Synth $ssml1 (Join-Path $out "test1.wav") "Microsoft Zira Desktop" (-2)

# test2: start -> chapter question -> off-topic question -> stop
$ssml2 = @"
<speak version='1.0' xml:lang='en-US'>
  in fee start
  <break time='10s'/>
  what is a storm surge
  <break time='14s'/>
  what is the capital of france
  <break time='12s'/>
  in fee stop
  <break time='4s'/>
</speak>
"@
Synth $ssml2 (Join-Path $out "test2.wav") "Microsoft Zira Desktop" (-2)
