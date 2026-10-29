@echo off
rem Lets Mirmera on the server use Qwen (Ollama) on this computer, through SSH.
rem The server's 127.0.0.1:11434 leads to this computer's 127.0.0.1:11434: nothing on this
rem computer is opened to the internet, and the server can reach only Ollama.
rem Keep this window open while you use Mirmera's AI; it reconnects by itself.
rem
rem   scripts\qwen-tunnel.cmd mirmera.example.com
if "%~1"=="" (
  echo Usage: scripts\qwen-tunnel.cmd SERVER
  exit /b 1
)
:loop
echo %time:~0,8% Connecting to tunnel@%~1 ...
ssh -N -i "%USERPROFILE%\.ssh\mirmera_tunnel" -o ServerAliveInterval=30 -o ServerAliveCountMax=3 -o ExitOnForwardFailure=yes -R 127.0.0.1:11434:127.0.0.1:11434 tunnel@%~1
echo %time:~0,8% The tunnel closed. Reconnecting in 10 seconds (Ctrl+C to stop).
timeout /t 10 /nobreak >nul
goto loop
