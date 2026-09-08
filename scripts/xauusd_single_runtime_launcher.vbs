Option Explicit

If WScript.Arguments.Count < 3 Or WScript.Arguments.Count > 4 Then
    WScript.Quit 2
End If

Function QuoteArgument(value)
    QuoteArgument = Chr(34) & value & Chr(34)
End Function

Dim controlScript, runtimeRoot, repositoryRoot
controlScript = WScript.Arguments(0)
runtimeRoot = WScript.Arguments(1)
repositoryRoot = WScript.Arguments(2)
Dim action
action = "Watchdog"
If WScript.Arguments.Count = 4 Then action = WScript.Arguments(3)
If action <> "Watchdog" And action <> "Gui" Then WScript.Quit 2
Dim command
command = "powershell.exe -NoProfile -STA -NonInteractive -ExecutionPolicy Bypass" _
    & " -File " & QuoteArgument(controlScript) _
    & " -Action " & action _
    & " -RuntimeRoot " & QuoteArgument(runtimeRoot) _
    & " -RepositoryRoot " & QuoteArgument(repositoryRoot)


Dim shell, exitCode
Set shell = CreateObject("WScript.Shell")
Do
    exitCode = shell.Run(command, 0, True)
    If exitCode = 75 Then WScript.Sleep 1000
Loop While exitCode = 75 And action = "Watchdog"
WScript.Quit exitCode
