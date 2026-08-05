namespace CAlgo.Robots
{
    using System;
    using System.Globalization;
    using System.IO;
    using System.Text;

    using cAlgo.API;

    [Robot(TimeZone = TimeZones.UTC, AccessRights = AccessRights.FullAccess)]
    public sealed class XauusdForwardQuoteBridge : Robot
    {
        private StreamWriter writer;
        private DateTime activeDateUtc;
        private string activePath;
        private long sequence;
        private long invalidQuotes;

        [Parameter("Output Directory", DefaultValue = "")]
        public string OutputDirectory
        {
            get; set;
        }

        [Parameter("Expected Symbol", DefaultValue = "XAUUSD")]
        public string ExpectedSymbol
        {
            get; set;
        }

        [Parameter("Flush Interval Seconds", DefaultValue = 1, MinValue = 1, MaxValue = 10)]
        public int FlushIntervalSeconds
        {
            get; set;
        }

        protected override void OnStart()
        {
            if (string.IsNullOrWhiteSpace(this.OutputDirectory))
            {
                throw new InvalidOperationException("OutputDirectory is required.");
            }

            if (!string.Equals(this.SymbolName, this.ExpectedSymbol, StringComparison.OrdinalIgnoreCase))
            {
                throw new InvalidOperationException(
                    string.Format(
                        CultureInfo.InvariantCulture,
                        "Attached symbol {0} does not match ExpectedSymbol {1}.",
                        this.SymbolName,
                        this.ExpectedSymbol));
            }

            string outputDirectory = Path.GetFullPath(this.OutputDirectory.Trim());
            Directory.CreateDirectory(outputDirectory);
            this.OutputDirectory = outputDirectory;
            this.OpenDailyFile(this.UtcNow());
            this.Timer.Start(TimeSpan.FromSeconds(Math.Max(1, this.FlushIntervalSeconds)));
            this.Print(
                "XAU_FORWARD_BRIDGE|start|symbol={0}|output={1}|orders=disabled",
                this.SymbolName,
                outputDirectory);
        }

        protected override void OnTick()
        {
            DateTime eventTime = DateTime.SpecifyKind(this.Server.Time, DateTimeKind.Utc);
            DateTime receivedTime = this.UtcNow();
            if (eventTime.Date != this.activeDateUtc)
            {
                this.RotateDailyFile(eventTime);
            }

            double bid = this.Symbol.Bid;
            double ask = this.Symbol.Ask;
            if (!double.IsFinite(bid) || !double.IsFinite(ask) || bid <= 0.0d || ask < bid)
            {
                this.invalidQuotes += 1;
                return;
            }

            this.sequence += 1;
            this.writer.Write('{');
            this.writer.Write("\"schema\":\"xauusd.forward.quote.v1\",");
            this.writer.Write("\"source\":\"ctrader-cli\",");
            this.writer.Write("\"symbol\":\"");
            this.writer.Write(this.EscapeJson(this.SymbolName));
            this.writer.Write("\",");
            this.writer.Write("\"event_time\":\"");
            this.writer.Write(eventTime.ToString("O", CultureInfo.InvariantCulture));
            this.writer.Write("\",");
            this.writer.Write("\"received_time\":\"");
            this.writer.Write(receivedTime.ToString("O", CultureInfo.InvariantCulture));
            this.writer.Write("\",");
            this.writer.Write("\"bid\":");
            this.writer.Write(bid.ToString("R", CultureInfo.InvariantCulture));
            this.writer.Write(',');
            this.writer.Write("\"ask\":");
            this.writer.Write(ask.ToString("R", CultureInfo.InvariantCulture));
            this.writer.Write(',');
            this.writer.Write("\"sequence\":");
            this.writer.Write(this.sequence.ToString(CultureInfo.InvariantCulture));
            this.writer.WriteLine('}');
        }

        protected override void OnTimer()
        {
            if (this.writer != null)
            {
                this.writer.Flush();
            }
        }

        protected override void OnStop()
        {
            this.Timer.Stop();
            if (this.writer != null)
            {
                this.writer.Flush();
                this.writer.Dispose();
                this.writer = null;
            }

            this.Print(
                "XAU_FORWARD_BRIDGE|stop|symbol={0}|rows={1}|invalid_quotes={2}|path={3}",
                this.SymbolName,
                this.sequence,
                this.invalidQuotes,
                this.activePath ?? string.Empty);
        }

        private DateTime UtcNow()
        {
            return DateTime.UtcNow;
        }

        private void RotateDailyFile(DateTime eventTime)
        {
            this.writer.Flush();
            this.writer.Dispose();
            this.writer = null;
            this.OpenDailyFile(eventTime);
        }

        private void OpenDailyFile(DateTime timestamp)
        {
            this.activeDateUtc = timestamp.Date;
            string symbol = this.SanitizeFileSegment(this.SymbolName).ToLowerInvariant();
            this.activePath = Path.Combine(
                this.OutputDirectory,
                string.Format(
                    CultureInfo.InvariantCulture,
                    "{0}-quotes-{1:yyyyMMdd}.jsonl",
                    symbol,
                    this.activeDateUtc));
            FileStream stream = new FileStream(
                this.activePath,
                FileMode.Append,
                FileAccess.Write,
                FileShare.Read,
                64 * 1024,
                FileOptions.SequentialScan);
            this.writer = new StreamWriter(stream, new UTF8Encoding(false), 64 * 1024);
        }

        private string SanitizeFileSegment(string value)
        {
            StringBuilder builder = new StringBuilder(value.Length);
            foreach (char character in value)
            {
                builder.Append(Array.IndexOf(Path.GetInvalidFileNameChars(), character) >= 0 ? '_' : character);
            }

            return builder.ToString();
        }

        private string EscapeJson(string value)
        {
            return value.Replace("\\", "\\\\").Replace("\"", "\\\"");
        }
    }
}
