import { type FC } from "react";
import JSONPretty from "react-json-pretty";
import colors from "tailwindcss/colors";

import { agentPrismPrefix } from "../theme";

export interface JsonViewerProps {
  content: string;
  id: string;
  className?: string;
}

const wrapStyle = `font-size: 12px; white-space: pre-wrap; word-wrap: break-word; overflow-wrap: break-word;`;

export const DetailsViewJsonOutput: FC<JsonViewerProps> = ({
  content,
  id,
  className = "",
}) => {
  return (
    <JSONPretty
      booleanStyle={`color: ${colors.blue[800]};`}
      className={`min-w-0 overflow-x-auto rounded-xl p-4 text-left ${className}`}
      data={content}
      id={`json-pretty-${id}`}
      keyStyle={`color: oklch(var(--${agentPrismPrefix}-code-key));`}
      mainStyle={`color: oklch(var(--${agentPrismPrefix}-code-base)); ${wrapStyle}`}
      errorStyle={`color: oklch(var(--${agentPrismPrefix}-code-base)); ${wrapStyle}`}
      stringStyle={`color: oklch(var(--${agentPrismPrefix}-code-string));`}
      valueStyle={`color: oklch(var(--${agentPrismPrefix}-code-number));`}
    />
  );
};
