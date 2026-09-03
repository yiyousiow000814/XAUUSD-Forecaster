Option Explicit

If WScript.Arguments.Count <> 7 Then
    WScript.Quit 2
End If

Function QuoteArgument(value)
    QuoteArgument = Chr(34) & value & Chr(34)
End Function

Dim guardScript, taskName, heartbeatPath, ownerReceiptPath, controlScript, runtimeRoot, repositoryRoot
guardScript = WScript.Arguments(0)
taskName = WScript.Arguments(1)
heartbeatPath = WScript.Arguments(2)
ownerReceiptPath = WScript.Arguments(3)
controlScript = WScript.Arguments(4)
runtimeRoot = WScript.Arguments(5)
repositoryRoot = WScript.Arguments(6)

Dim command
command = "powershell.exe -NoProfile -NonInteractive -WindowStyle Hidden -ExecutionPolicy Bypass" _
    & " -File " & QuoteArgument(guardScript) _
    & " -TaskName " & QuoteArgument(taskName) _
    & " -HeartbeatPath " & QuoteArgument(heartbeatPath) _
    & " -OwnerReceiptPath " & QuoteArgument(ownerReceiptPath) _
    & " -ControlScript " & QuoteArgument(controlScript) _
    & " -RuntimeRoot " & QuoteArgument(runtimeRoot) _
    & " -RepositoryRoot " & QuoteArgument(repositoryRoot)

Dim shell, exitCode
Set shell = CreateObject("WScript.Shell")
exitCode = shell.Run(command, 0, True)
WScript.Quit exitCode
