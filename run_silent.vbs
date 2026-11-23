Set WshShell = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")
currentDir = fso.GetParentFolderName(WScript.ScriptFullName)
WshShell.CurrentDirectory = currentDir

pythonwPath = "pythonw.exe"
candidates = Array( _
    "C:\Program Files\Python314\pythonw.exe", _
    "C:\Program Files\Python313\pythonw.exe", _
    "C:\Program Files\Python312\pythonw.exe", _
    "C:\Program Files\Python311\pythonw.exe", _
    WshShell.ExpandEnvironmentStrings("%LOCALAPPDATA%\Programs\Python\Python314\pythonw.exe"), _
    WshShell.ExpandEnvironmentStrings("%LOCALAPPDATA%\Programs\Python\Python313\pythonw.exe"), _
    WshShell.ExpandEnvironmentStrings("%LOCALAPPDATA%\Programs\Python\Python312\pythonw.exe"), _
    WshShell.ExpandEnvironmentStrings("%LOCALAPPDATA%\Programs\Python\Python311\pythonw.exe") _
)

For Each p In candidates
    If fso.FileExists(p) Then
        pythonwPath = p
        Exit For
    End If
Next

WshShell.Run """" & pythonwPath & """ daemon.py", 0, False
