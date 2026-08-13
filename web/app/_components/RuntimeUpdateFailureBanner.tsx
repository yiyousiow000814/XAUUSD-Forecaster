export type RuntimeUpdateFailure = {
  status: string;
  message: string;
  failed_at: string;
};

function failureLabel(status: string): string {
  if (status === "ROLLED_BACK") return "新版运行验证失败，已自动恢复上一版。";
  if (status === "ROLLBACK_FAILED") return "新版运行验证失败，自动恢复也失败，请检查本机服务。";
  if (status === "SWITCH_FAILED") return "新版切换失败，当前版本继续运行。";
  return "新版预检失败，当前版本继续运行。";
}

export default function RuntimeUpdateFailureBanner({ failure }: { failure?: RuntimeUpdateFailure | null }) {
  if (!failure) return null;
  return <div className="error-banner" role="alert">
    <b>{failureLabel(failure.status)}</b>
    <small>{failure.message}</small>
  </div>;
}
