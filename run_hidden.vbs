' Push-to-Talk silent launcher.
' Runs push_to_talk.py with pythonw.exe (GUI Python) so there is NO console
' window at all. Double-click to start Push-to-Talk invisibly in the background.

Option Explicit

Dim shell, fso, scriptDir, pyScript, pythonw, cmd

Set shell = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")

' Folder where this .vbs lives.
scriptDir = fso.GetParentFolderName(WScript.ScriptFullName)
pyScript = scriptDir & "\push_to_talk.py"

' Locate pythonw.exe. First try the known install dir, then fall back to PATH.
pythonw = ""

Dim candidates, c
candidates = Array( _
    shell.ExpandEnvironmentStrings("%LOCALAPPDATA%") & "\Programs\Python\Python311\pythonw.exe", _
    shell.ExpandEnvironmentStrings("%LOCALAPPDATA%") & "\Programs\Python\Python312\pythonw.exe", _
    shell.ExpandEnvironmentStrings("%LOCALAPPDATA%") & "\Programs\Python\Python310\pythonw.exe" _
)

For Each c In candidates
    If pythonw = "" And fso.FileExists(c) Then
        pythonw = c
    End If
Next

' Last resort: rely on PATH.
If pythonw = "" Then
    pythonw = "pythonw.exe"
End If

' Build the command. Extra quotes handle paths with spaces / Cyrillic names.
cmd = """" & pythonw & """ """ & pyScript & """"

' Window style 0 = hidden; False = don't wait.
shell.Run cmd, 0, False
