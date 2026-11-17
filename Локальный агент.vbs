' Запуск без окна консоли.
Set shell = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")
root = fso.GetParentFolderName(WScript.ScriptFullName)
shell.CurrentDirectory = root
shell.Run "cmd /c set PYTHONUTF8=1 && py -3 -m app.main", 0, False
