# One-time downloads for the voice window (run with internet; safe to re-run, it resumes):
#     powershell -ExecutionPolicy Bypass -File voice\get_models.ps1
# Whisper base.en is about 145 MB. Add  -Size small.en  for the more accurate 480 MB model.
param([string]$Size = "base.en")
$env:HF_HUB_DISABLE_XET = "1"          # plain HTTP download: slower links do not stall
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
Write-Host "== 1/3 Python packages"
python -m pip install --user faster-whisper sounddevice matplotlib
Write-Host "== 2/3 Whisper $Size -> voice\models\$Size"
python -c "from huggingface_hub import snapshot_download; import sys; snapshot_download('Systran/faster-whisper-' + sys.argv[1], local_dir=sys.argv[2], allow_patterns=['config.json','model.bin','tokenizer.json','vocabulary.*']); print('Whisper model ready')" $Size "$here\models\$Size"
Write-Host "== 3/3 Microphones (use the number with --mic) and Ollama models"
python -m sounddevice
ollama list
Write-Host "Done."
