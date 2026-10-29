# Lets Mirmera on the server use Qwen (Ollama) on this computer, through SSH.
# The server's 127.0.0.1:11434 leads to this computer's 127.0.0.1:11434: nothing on this
# computer is opened to the internet, and the server can reach only Ollama.
# Keep this window open while you use Mirmera's AI; it reconnects by itself.
#
#   .\scripts\qwen-tunnel.ps1 -Server mirmera.example.com
param(
  [Parameter(Mandatory = $true)][string]$Server,
  [string]$User = "tunnel",
  [string]$Key = "$HOME\.ssh\mirmera_tunnel"
)
try { Invoke-RestMethod -TimeoutSec 5 http://127.0.0.1:11434/api/tags | Out-Null }
catch { Write-Warning "Ollama isn't answering on this computer. Open the Ollama app (or run: ollama serve)." }
while ($true) {
  Write-Host "$(Get-Date -Format HH:mm:ss) Connecting to $User@$Server ..."
  ssh -N -i $Key -o ServerAliveInterval=30 -o ServerAliveCountMax=3 -o ExitOnForwardFailure=yes `
      -R 127.0.0.1:11434:127.0.0.1:11434 "$User@$Server"
  Write-Host "$(Get-Date -Format HH:mm:ss) The tunnel closed. Reconnecting in 10 seconds (Ctrl+C to stop)."
  Start-Sleep -Seconds 10
}
