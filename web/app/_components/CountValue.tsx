import { countPresentation, type CountFormat } from "../_lib/count-format";

export default function CountValue({
  value,
  format = "compact",
  suffix = "",
}: {
  value: number | null | undefined;
  format?: CountFormat;
  suffix?: string;
}) {
  const { accessibleValue, display, exact, title } = countPresentation(value, format, suffix);

  return <span
    className="count-value"
    aria-label={accessibleValue}
    data-exact-count={exact === "—" ? undefined : exact}
    title={title}
  >{display}{suffix}</span>;
}
