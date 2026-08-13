import { runtimeUpdateFailurePresentation } from "../_lib/runtime-update-failure";

export type RuntimeUpdateFailure = {
  status: string;
  failed_at: string;
};

export default function RuntimeUpdateFailureBanner({ failure }: { failure?: RuntimeUpdateFailure | null }) {
  const presentation = runtimeUpdateFailurePresentation(failure);
  if (!presentation) return null;
  return <div className="error-banner" role="alert">
    <b>{presentation.label}</b>
  </div>;
}
