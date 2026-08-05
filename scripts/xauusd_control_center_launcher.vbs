Option Explicit

Dim shell, fileSystem, scriptDirectory, controlScript, command
Set shell = CreateObject("WScript.Shell")
Set fileSystem = CreateObject("Scripting.FileSystemObject")
scriptDirectory = fileSystem.GetParentFolderName(WScript.ScriptFullName)
controlScript = fileSystem.BuildPath(scriptDirectory, "xauusd_control_center.ps1")
command = "powershell.exe -NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File """ _
    & controlScript & """ -Action Gui"
shell.Run command, 1, False
