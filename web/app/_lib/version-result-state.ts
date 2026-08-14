export type VersionEvaluationStatus =
  | "HAS_RESULTS"
  | "AWAITING_OUTCOME"
  | "OUTCOME_UNAVAILABLE"
  | "AWAITING_FIRST_PREDICTION"
  | "NO_PREDICTIONS";

type VersionResultMetric = {
  oos_rows: number;
  evaluation_status?: VersionEvaluationStatus;
};

export function versionResultLabel(
  metric: VersionResultMetric,
  formattedReturn: string,
): string {
  if (metric.oos_rows > 0) return formattedReturn;
  if (metric.evaluation_status === "AWAITING_OUTCOME") return "等待结果";
  if (metric.evaluation_status === "OUTCOME_UNAVAILABLE") return "结果缺失";
  if (metric.evaluation_status === "AWAITING_FIRST_PREDICTION") return "等待首个预测";
  if (metric.evaluation_status === "NO_PREDICTIONS") return "未产生结果";
  return "状态未知";
}
