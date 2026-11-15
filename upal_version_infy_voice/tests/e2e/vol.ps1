Add-Type -TypeDefinition @"
using System.Runtime.InteropServices;
[Guid("5CDF2C82-841E-4546-9722-0CF74078229A"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
interface IAudioEndpointVolume {
  int f(); int g(); int h(); int i();
  int SetMasterVolumeLevelScalar(float fLevel, System.Guid pguidEventContext);
  int j(); int GetMasterVolumeLevelScalar(out float pfLevel);
  int k(); int l(); int m(); int n();
  int SetMute([MarshalAs(UnmanagedType.Bool)] bool bMute, System.Guid pguidEventContext);
  int GetMute(out bool pbMute);
}
[Guid("D666063F-1587-4E43-81F1-B948E807363F"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
interface IMMDevice { int Activate(ref System.Guid id, int clsCtx, int activationParams, out IAudioEndpointVolume aev); }
[Guid("A95664D2-9614-4F35-A746-DE8DB63617E6"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
interface IMMDeviceEnumerator { int f(); int GetDefaultAudioEndpoint(int dataFlow, int role, out IMMDevice endpoint); }
[ComImport, Guid("BCDE0395-E52F-467C-8E3D-C4579291692E")] class MMDeviceEnumeratorComObject { }
public class Vol {
  static IAudioEndpointVolume V(int flow) {
    var e = new MMDeviceEnumeratorComObject() as IMMDeviceEnumerator; IMMDevice d = null;
    Marshal.ThrowExceptionForHR(e.GetDefaultAudioEndpoint(flow, 1, out d));
    var g = typeof(IAudioEndpointVolume).GUID; IAudioEndpointVolume v = null;
    Marshal.ThrowExceptionForHR(d.Activate(ref g, 23, 0, out v)); return v; }
  public static string Get(int flow) { var v = V(flow); float l; bool m; v.GetMasterVolumeLevelScalar(out l); v.GetMute(out m); return "level=" + l + " mute=" + m; }
}
"@
"speaker: " + [Vol]::Get(0)
"mic:     " + [Vol]::Get(1)
