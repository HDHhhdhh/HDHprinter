Option Explicit

Dim shell, fso, appDir, appPath, venvPythonPath, python312Path, command
Set shell = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")

appDir = fso.GetParentFolderName(WScript.ScriptFullName)
appPath = appDir & "\unit_print_manager.py"
venvPythonPath = appDir & "\.venv\Scripts\pythonw.exe"
python312Path = shell.ExpandEnvironmentStrings("%LOCALAPPDATA%") & "\Programs\Python\Python312\pythonw.exe"
shell.CurrentDirectory = appDir

If fso.FileExists(venvPythonPath) Then
  command = Chr(34) & venvPythonPath & Chr(34) & " " & Chr(34) & appPath & Chr(34)
ElseIf fso.FileExists(python312Path) Then
  command = Chr(34) & python312Path & Chr(34) & " " & Chr(34) & appPath & Chr(34)
Else
  command = "pythonw " & Chr(34) & appPath & Chr(34)
End If

shell.Run command, 0, False
