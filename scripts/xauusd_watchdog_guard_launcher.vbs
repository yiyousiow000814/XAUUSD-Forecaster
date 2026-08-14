Option Explicit

If WScript.Arguments.Count <> 3 Then
    WScript.Quit 2
End If

Function QuoteArgument(value)
    QuoteArgument = Chr(34) & value & Chr(34)
End Function

Dim guardScript, taskName, heartbeatPath
guardScript = WScript.Arguments(0)
taskName = WScript.Arguments(1)
heartbeatPath = WScript.Arguments(2)

Dim command
command = "powershell.exe -NoProfile -NonInteractive -WindowStyle Hidden -ExecutionPolicy Bypass" _
    & " -File " & QuoteArgument(guardScript) _
    & " -TaskName " & QuoteArgument(taskName) _
    & " -HeartbeatPath " & QuoteArgument(heartbeatPath)

Dim shell, exitCode
Set shell = CreateObject("WScript.Shell")
exitCode = shell.Run(command, 0, True)
WScript.Quit exitCode
