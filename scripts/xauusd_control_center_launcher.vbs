Option Explicit

Dim shell, fileSystem, scriptDirectory, repositoryRoot, runtimeRoot, controlScript, command
Set shell = CreateObject("WScript.Shell")
Set fileSystem = CreateObject("Scripting.FileSystemObject")
scriptDirectory = fileSystem.GetParentFolderName(WScript.ScriptFullName)
repositoryRoot = fileSystem.GetParentFolderName(fileSystem.GetParentFolderName(scriptDirectory))
runtimeRoot = repositoryRoot
If WScript.Arguments.Count > 0 Then runtimeRoot = WScript.Arguments(0)
If WScript.Arguments.Count > 1 Then repositoryRoot = WScript.Arguments(1)
controlScript = fileSystem.BuildPath(scriptDirectory, "xauusd_control_center.ps1")
command = "powershell.exe -NoProfile -STA -WindowStyle Hidden -ExecutionPolicy Bypass -File """ _
    & controlScript & """ -Action Gui -RuntimeRoot """ & runtimeRoot _
    & """ -RepositoryRoot """ & repositoryRoot & """"
shell.Run command, 0, False
