import type { TraceSpan } from "@evilmartians/agent-prism-types";

import { type ReactElement } from "react";

import { CopyButton } from "../CopyButton";
import { DetailsViewJsonOutput } from "./DetailsViewJsonOutput";

interface RawDataTabProps {
  data: TraceSpan;
}

export const DetailsViewRawDataTab = ({
  data,
}: RawDataTabProps): ReactElement => (
  <div className="border-agentprism-border min-w-0 w-full rounded-md border bg-transparent">
    <div className="flex justify-end p-1.5">
      <CopyButton label="Raw" content={data.raw} />
    </div>

    <div className="pb-4">
      <DetailsViewJsonOutput
        content={data.raw}
        id={data.id || "span-details"}
      />
    </div>
  </div>
);
