using System;
using System.Net;
using System.Net.Sockets;
using System.Text;

// Playwright expects a Windows executable. Pass only its connect-page URL to
// the Runtime's short-lived broker, which authenticates and validates it.
internal static class BrowserConnect
{
    private static int Main(string[] args)
    {
        int port;
        string token = Environment.GetEnvironmentVariable("EIDOS_BROWSER_BROKER_TOKEN");
        if (!Int32.TryParse(Environment.GetEnvironmentVariable("EIDOS_BROWSER_BROKER_PORT"), out port)
            || port <= 0 || port > 65535 || token == null || token.Length != 43 || args.Length == 0)
            return 1;

        string url = args[args.Length - 1];
        if (url.Length == 0 || url.Length > 4096 || url.IndexOfAny(new char[] { '\r', '\n', '\0' }) >= 0)
            return 1;

        try
        {
            using (TcpClient client = new TcpClient(AddressFamily.InterNetwork))
            {
                IAsyncResult connection = client.BeginConnect(IPAddress.Loopback, port, null, null);
                using (var ready = connection.AsyncWaitHandle)
                {
                    if (!ready.WaitOne(1500))
                        return 1;
                    client.EndConnect(connection);
                }
                client.SendTimeout = 1500;
                byte[] packet = Encoding.ASCII.GetBytes(token + " " + url + "\n");
                client.GetStream().Write(packet, 0, packet.Length);
            }
            return 0;
        }
        catch (SocketException) { return 1; }
        catch (System.IO.IOException) { return 1; }
    }
}
