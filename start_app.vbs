' Starts the factory console with no visible console window.
'
' This used to launch run_app.cmd and return immediately, so if the app
' failed to start the operator saw nothing at all - no window, no error,
' just a console that never came up. It now waits for the launcher and
' reports a non-zero exit so a failed start is visible on the factory PC.

Dim shell, fso, scriptDir, cmdPath, exitCode

Set shell = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")

scriptDir = fso.GetParentFolderName(WScript.ScriptFullName)
cmdPath = scriptDir & "\run_app.cmd"

If Not fso.FileExists(cmdPath) Then
    MsgBox "Cannot find run_app.cmd next to this shortcut." & vbCrLf & _
           "Expected: " & cmdPath, 16, "Al Sadi Knitwear OS"
    WScript.Quit 1
End If

' 0 = hidden window, True = wait so we can read the exit code.
exitCode = shell.Run(Chr(34) & cmdPath & Chr(34), 0, True)

If exitCode <> 0 Then
    MsgBox "Al Sadi Knitwear OS stopped unexpectedly (exit code " & exitCode & ")." & vbCrLf & vbCrLf & _
           "Check streamlit.log in:" & vbCrLf & scriptDir & vbCrLf & vbCrLf & _
           "If Streamlit is missing, run:" & vbCrLf & _
           "    python -m pip install -r requirements.txt", _
           16, "Al Sadi Knitwear OS"
End If

WScript.Quit exitCode
