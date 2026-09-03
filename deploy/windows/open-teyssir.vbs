' Open Teyssir ERP with no console flash.
' Used by the Desktop / Start Menu "Teyssir ERP" shortcut.
Option Explicit
Dim sh, fso, dir, ps1, cmd
Set fso = CreateObject("Scripting.FileSystemObject")
Set sh = CreateObject("WScript.Shell")
dir = fso.GetParentFolderName(WScript.ScriptFullName)
ps1 = dir & "\open-teyssir.ps1"
If Not fso.FileExists(ps1) Then
  WScript.Quit 1
End If
cmd = "powershell.exe -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File """ & ps1 & """"
' 0 = hidden window, False = do not wait
sh.Run cmd, 0, False
