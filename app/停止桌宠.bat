@echo off
rem Stop the running Mimo desktop pet (ASCII only: cmd reads .bat as the OEM
rem code page, so a hard-coded Chinese path would be mangled)
powershell -NoProfile -Command "Get-CimInstance Win32_Process -Filter \"Name='pythonw.exe'\" | Where-Object { $_.CommandLine -like '*mimo_pet.py*' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }; Get-CimInstance Win32_Process -Filter \"Name='MimoPet.exe'\" | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }"