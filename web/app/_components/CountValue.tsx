import { formatCount, formatExactCount, type CountFormat } from "../_lib/count-format";

export default function CountValue({
  value,
  format = "compact",
  suffix = "",
}: {
  value: number | null | undefined;
  format?: CountFormat;
  suffix?: string;
}) {
  const exact = formatExactCount(value);
  const display = formatCount(value, format);
  const accessibleValue = exact === "—" ? "暂无数据" : `${exact}${suffix}`;
  const isAbbreviated = exact !== "—" && display !== exact;

  return <span
    className="count-value"
    aria-label={accessibleValue}
    data-exact-count={exact === "—" ? undefined : exact}
    title={isAbbreviated ? `${exact}${suffix}` : undefined}
  >{display}{suffix}</span>;
}
