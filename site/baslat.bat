@echo off
setlocal
cd /d "%~dp0"
set "PORT=8000"

where py >nul 2>nul
if %errorlevel%==0 (
  echo Divanu Lugati't-Turk yerel sunucusu baslatiliyor: http://localhost:%PORT%
  start "Divanu Lugati't-Turk" http://localhost:%PORT%
  py -m http.server %PORT%
  goto :eof
)

where python >nul 2>nul
if %errorlevel%==0 (
  echo Divanu Lugati't-Turk yerel sunucusu baslatiliyor: http://localhost:%PORT%
  start "Divanu Lugati't-Turk" http://localhost:%PORT%
  python -m http.server %PORT%
  goto :eof
)

echo Python bulunamadi. Python 3 kurup baslat.bat dosyasini tekrar calistirin.
pause