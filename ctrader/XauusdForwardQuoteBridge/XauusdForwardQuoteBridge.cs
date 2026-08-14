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
        private string marketSessionPath;
        private long sequence;
        private long invalidQuotes;
        private long marketSessionWrites;

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
            this.marketSessionPath = Path.Combine(outputDirectory, "market-session.json");
            this.OpenDailyFile(this.UtcNow());
            this.WriteMarketSession();
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

            this.WriteMarketSession();
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
                "XAU_FORWARD_BRIDGE|stop|symbol={0}|rows={1}|invalid_quotes={2}|session_writes={3}|path={4}",
                this.SymbolName,
                this.sequence,
                this.invalidQuotes,
                this.marketSessionWrites,
                this.activePath ?? string.Empty);
        }

        private void WriteMarketSession()
        {
            DateTime observedAt = this.UtcNow();
            DateTime serverTime = DateTime.SpecifyKind(this.Server.Time, DateTimeKind.Utc);
            bool isOpen = this.Symbol.MarketHours.IsOpened();
            TimeSpan timeTillOpen = this.Symbol.MarketHours.TimeTillOpen();
            TimeSpan timeTillClose = this.Symbol.MarketHours.TimeTillClose();
            DateTime? nextOpenTime = isOpen ? null : serverTime.Add(timeTillOpen);
            DateTime? nextCloseTime = isOpen ? serverTime.Add(timeTillClose) : null;
            StringBuilder payload = new StringBuilder(512);
            payload.Append('{');
            payload.Append("\"schema\":\"xauusd.forward.market-session.v1\",");
            payload.Append("\"source\":\"ctrader-cli\",");
            payload.Append("\"symbol\":\"").Append(this.EscapeJson(this.SymbolName)).Append("\",");
            payload.Append("\"observed_at\":\"").Append(observedAt.ToString("O", CultureInfo.InvariantCulture)).Append("\",");
            payload.Append("\"server_time\":\"").Append(serverTime.ToString("O", CultureInfo.InvariantCulture)).Append("\",");
            payload.Append("\"is_open\":").Append(isOpen ? "true" : "false").Append(',');
            payload.Append("\"time_till_open_seconds\":").Append(Math.Max(0.0d, timeTillOpen.TotalSeconds).ToString("R", CultureInfo.InvariantCulture)).Append(',');
            payload.Append("\"time_till_close_seconds\":").Append(Math.Max(0.0d, timeTillClose.TotalSeconds).ToString("R", CultureInfo.InvariantCulture)).Append(',');
            payload.Append("\"next_open_time\":").Append(this.JsonTimestamp(nextOpenTime)).Append(',');
            payload.Append("\"next_close_time\":").Append(this.JsonTimestamp(nextCloseTime));
            payload.Append('}');

            string temporaryPath = this.marketSessionPath + ".tmp";
            try
            {
                System.IO.File.WriteAllText(temporaryPath, payload.ToString(), new UTF8Encoding(false));
                System.IO.File.Move(temporaryPath, this.marketSessionPath, true);
                this.marketSessionWrites += 1;
            }
            finally
            {
                if (System.IO.File.Exists(temporaryPath))
                {
                    System.IO.File.Delete(temporaryPath);
                }
            }
        }

        private string JsonTimestamp(DateTime? value)
        {
            return value.HasValue
                ? "\"" + value.Value.ToString("O", CultureInfo.InvariantCulture) + "\""
                : "null";
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
