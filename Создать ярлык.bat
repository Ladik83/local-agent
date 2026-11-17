@echo off
chcp 65001 >nul
set ROOT=%~dp0
powershell -NoProfile -Command ^
  "$s=(New-Object -ComObject WScript.Shell).CreateShortcut([Environment]::GetFolderPath('Desktop')+'\Локальный агент.lnk');" ^
  "$s.TargetPath='%ROOT%Локальный агент.vbs';" ^
  "$s.WorkingDirectory='%ROOT%';" ^
  "$s.Description='Свой ИИ-агент на этом компьютере';" ^
  "$s.Save()"
echo Ярлык на рабочем столе создан.
pause
