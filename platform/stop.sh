#!/usr/bin/env bash
# Stop the SSR Platform: kills whatever Windows processes hold the backend (:8001)
# and frontend (:5173) ports, however they were started, plus the portless
# events worker (matched by command line).
# ponytail: kill-by-port; an orphaned npm/cmd wrapper may linger harmlessly.
for port in 8001 5173; do
  powershell.exe -NoProfile -Command "
    \$c = Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue
    if (\$c) {
      \$c | Select-Object -ExpandProperty OwningProcess -Unique |
        ForEach-Object { Stop-Process -Id \$_ -Force -ErrorAction SilentlyContinue }
      Write-Output 'port $port: stopped'
    } else {
      Write-Output 'port $port: nothing running'
    }"
done
powershell.exe -NoProfile -Command "
  \$w = Get-CimInstance Win32_Process -Filter \"Name like 'python%'\" |
    Where-Object { \$_.CommandLine -match 'app\.worker' }
  if (\$w) {
    \$w | ForEach-Object { Stop-Process -Id \$_.ProcessId -Force -ErrorAction SilentlyContinue }
    Write-Output 'worker: stopped'
  } else {
    Write-Output 'worker: nothing running'
  }"
