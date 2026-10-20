import { modelIdentityView } from "../modelSelection";
import type { ExecutionMetadata, ModelOptionsResponse } from "../types";

export function ModelIdentity({ metadata, catalog }: {
  metadata: ExecutionMetadata; catalog?: ModelOptionsResponse | null;
}) {
  const view = modelIdentityView(metadata, catalog);
  return <section className="model-identity" aria-label="Job model identity">
    <dl>
      <div><dt>Requested model</dt><dd>{view.requested}</dd></div>
      {view.resolved && <div><dt>Resolved server default</dt><dd>{view.resolved}</dd></div>}
      <div><dt>Actual model</dt><dd>{view.actual}</dd></div>
    </dl>
    {view.mismatch && <p className="model-identity-warning" role="alert">
      Model identity mismatch: the recorded execution does not match the requested or resolved model.
    </p>}
  </section>;
}
