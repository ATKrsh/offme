Set WshShell = CreateObject("WScript.Shell")
WshShell.Run "pythonw """ & WshShell.CurrentDirectory & "\offme.py""", 0, False
