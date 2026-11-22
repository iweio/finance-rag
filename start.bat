@echo off
REM One-command startup for Windows demo machines. Same contract as start.sh.
REM
REM ASCII ONLY on purpose: cmd.exe reads .bat with the OEM codepage, and UTF-8
REM Chinese bytes can eat a closing paren or a newline, which silently breaks the
REM `if errorlevel` blocks below. Chinese messages belong in start.sh / the docs.
setlocal enabledelayedexpansion
cd /d "%~dp0"

REM On a clean machine nginx answers on 80. If port 80 is taken on this machine
REM (IIS / http.sys) use docker-compose.override.yml to move it and set the same
REM value here, e.g.  set BASE_URL=http://localhost:8080
if "%BASE_URL%"=="" set BASE_URL=http://localhost

echo ==^> checking Docker
where docker >nul 2>nul
if errorlevel 1 (
  echo [FAIL] docker not found. Install Docker Desktop, start it, then run this again.
  exit /b 1
)
docker compose version >nul 2>nul
if errorlevel 1 (
  echo [FAIL] docker compose v2 not found.
  exit /b 1
)

if not exist .env (
  copy /y .env.example .env >nul
  echo ==^> created .env from .env.example
  echo     fill in two values before continuing: LLM_API_KEY, JWT_SECRET
)

findstr /c:"sk-xxxxxxxx" .env >nul && echo [WARN] LLM_API_KEY is still the placeholder, /chat will fail
findstr /c:"change-me-to-a-long-random-string" .env >nul && echo [WARN] JWT_SECRET is still the placeholder

echo ==^> building and starting 7 services (first run 10~20 min)
docker compose up -d --build
if errorlevel 1 (
  echo [FAIL] docker compose up failed, see output above.
  exit /b 1
)

echo ==^> waiting for %BASE_URL%/api/v1/health (up to 10 min)
set /a tries=0
:wait
set /a tries+=1
for /f %%c in ('curl -s -o nul -w "%%{http_code}" %BASE_URL%/api/v1/health') do set code=%%c
if "%code%"=="200" goto ready
if %tries% GEQ 120 (
  echo [FAIL] health did not pass within 10 minutes.
  docker compose ps
  docker compose logs --tail=80 backend
  exit /b 1
)
timeout /t 5 /nobreak >nul
goto wait

:ready
echo.
echo ==^> health:
curl -s %BASE_URL%/api/v1/health
echo.
echo.
echo   frontend     %BASE_URL%
echo   API docs     http://localhost:8000/docs
echo   graph stats  curl -s %BASE_URL%/api/v1/graph/stats
echo.
docker compose ps
endlocal
