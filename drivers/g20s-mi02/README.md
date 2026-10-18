# G20S MI_02 device ownership

This folder contains the narrowly matched WinUSB INF used by the optional
`owned` input mode. Its hardware ID is exactly
`USB\VID_4842&PID_0001&MI_02`; it must never be broadened to the composite
receiver or the USB/HID keyboard classes.

The INF is deliberately not installable as checked in: Windows x64 requires a
signed catalog, and RunLights does not generate a certificate, trust one, or
install a driver without explicit approval. The in-box Microsoft `WinUSB.sys`
binary is used; RunLights supplies no kernel binary.

Before installation, record the current instance and driver:

```powershell
pnputil /enum-devices /instanceid "USB\VID_4842&PID_0001&MI_02\6&2e5db5d6&0&0002" /drivers
```

After a signed package is installed, record the assigned `oemNN.inf`. Complete
rollback is:

```powershell
pnputil /delete-driver oemNN.inf /uninstall /force
pnputil /scan-devices
```

Windows then restores the Microsoft HID `input.inf` binding. Unplugging and
reconnecting the receiver may be required. Do not run the rollback with a
guessed `oemNN.inf`; verify its provider, hardware ID, and original name first.
